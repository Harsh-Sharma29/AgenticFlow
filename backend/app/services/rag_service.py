"""Asynchronous Hybrid RAG (Retrieval-Augmented Generation) service.

Combines FAISS vector-store (semantic search) with Memgraph knowledge-graph
(relationship search) for production-grade GraphRAG. All public methods are
``async def`` so they integrate cleanly with the async LangGraph nodes.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings

from backend.app.config import get_settings
from backend.app.services.local_ingestion import LocalDocumentIngestor
from backend.app.services.graph_service import MemgraphService
from backend.app.utils.filenames import resolve_workspace_doc_path

logger = logging.getLogger(__name__)


class RAGService:
    """Async-ready Hybrid RAG service: FAISS (vector) + Memgraph (graph).

    The service maintains an in-memory cache of session-scoped vector stores
    and a shared Memgraph connection for knowledge-graph operations.
    """

    def __init__(
        self,
        embedding_model: str = "BAAI/bge-small-en-v1.5",
        chunk_size: int = 800,
        chunk_overlap: int = 100,
    ) -> None:
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self._embedding_model_name = embedding_model

        # Local CPU embedding model for FAISS compatibility
        self._embeddings: Optional[HuggingFaceEmbeddings] = None

        # Local ingestion pipeline (PyMuPDF + sentence-transformers)
        self.ingestor = LocalDocumentIngestor(
            model_name=embedding_model,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            batch_size=32,
        )

        # Memgraph knowledge-graph service (graceful degradation if unavailable)
        self.graph_service = MemgraphService()
        if self.graph_service.is_available:
            self.graph_service.ensure_indexes()

        # Session-scoped FAISS vector stores cached in-memory
        self.vector_stores: Dict[str, FAISS] = {}

    @property
    def embeddings(self) -> HuggingFaceEmbeddings:
        """Lazily initialise the local HuggingFace embedding model for FAISS."""
        if self._embeddings is None:
            logger.info("Initializing HuggingFaceEmbeddings for FAISS: %s", self._embedding_model_name)
            self._embeddings = HuggingFaceEmbeddings(
                model_name=self._embedding_model_name,
                model_kwargs={"device": "cpu"},
                encode_kwargs={"normalize_embeddings": True, "batch_size": 32},
            )
        return self._embeddings

    def _cache_key(self, user_id: str, session_id: str) -> str:
        return f"{user_id}::{session_id}"

    def invalidate_session_store(self, user_id: str, session_id: str) -> None:
        key = self._cache_key(user_id, session_id)
        self.vector_stores.pop(key, None)
        logger.info("Invalidated FAISS cache for key=%s", key)

    async def get_or_load_store(
        self,
        user_id: str = "guest",
        session_id: str = "default",
        vector_index_path: Optional[str] = None,
        errors: Optional[List[str]] = None,
    ) -> Optional[FAISS]:
        key = self._cache_key(user_id, session_id)

        if key in self.vector_stores:
            return self.vector_stores[key]

        if vector_index_path and os.path.isdir(vector_index_path):
            try:
                store = await asyncio.to_thread(
                    FAISS.load_local,
                    vector_index_path,
                    self.embeddings,
                    allow_dangerous_deserialization=True,
                )
                self.vector_stores[key] = store
                return store
            except Exception as exc:
                msg = f"Failed to load FAISS index at {vector_index_path}: {exc}"
                logger.warning(msg)
                if errors is not None:
                    errors.append(msg)

        return None

    async def load_documents(
        self,
        doc_paths: List[str],
        user_id: str = "guest",
        session_id: str = "default",
        vector_index_path: Optional[str] = None,
        errors: Optional[List[str]] = None,
        llm_router: Any = None,
    ) -> int:
        """Load, chunk, embed and index documents into FAISS + Memgraph.

        If llm_router is provided and Memgraph is available, entities and
        relationships are also extracted and stored in the knowledge graph.
        """
        if not doc_paths:
            return 0

        all_documents: List[Document] = []
        all_chunks_by_file: Dict[str, List[str]] = {}

        for raw_path in doc_paths:
            doc_path = resolve_workspace_doc_path(raw_path)
            if not os.path.exists(doc_path):
                if errors is not None:
                    errors.append(f"Document not found: {raw_path}")
                continue

            try:
                logger.info("Processing document with local pipeline: %s", doc_path)

                if doc_path.lower().endswith(".pdf"):
                    text = await asyncio.to_thread(self.ingestor.extract_pdf_text, doc_path)
                elif doc_path.lower().endswith((".txt", ".md")):
                    with open(doc_path, "r", encoding="utf-8") as f:
                        text = f.read()
                else:
                    if errors is not None:
                        errors.append(f"Unsupported file type: {doc_path}")
                    continue

                chunks = await asyncio.to_thread(self.ingestor.chunk_text, text)
                filename = os.path.basename(doc_path)
                all_chunks_by_file[filename] = chunks

                for chunk in chunks:
                    all_documents.append(
                        Document(
                            page_content=chunk,
                            metadata={"source": doc_path, "filename": filename},
                        )
                    )

            except Exception as exc:
                if errors is not None:
                    errors.append(f"Error loading {doc_path}: {exc}")
                logger.exception("Error loading document %s", doc_path)

        if not all_documents:
            return 0

        # ── FAISS indexing ──────────────────────────────────────────────
        key = self._cache_key(user_id, session_id)
        store = await self.get_or_load_store(user_id, session_id, vector_index_path, errors)

        logger.info("Embedding and indexing %d chunks into FAISS...", len(all_documents))
        if store is None:
            store = await asyncio.to_thread(FAISS.from_documents, all_documents, self.embeddings)
            self.vector_stores[key] = store
        else:
            await asyncio.to_thread(store.add_documents, all_documents)

        logger.info("FAISS indexing completed for session key=%s", key)

        if vector_index_path:
            try:
                os.makedirs(vector_index_path, exist_ok=True)
                await asyncio.to_thread(store.save_local, vector_index_path)
            except Exception as exc:
                if errors is not None:
                    errors.append(f"Failed to save FAISS index: {exc}")

        # ── Memgraph graph extraction (parallel, non-blocking) ──────────
        if llm_router and self.graph_service.is_available:
            for filename, chunks in all_chunks_by_file.items():
                try:
                    ent_count, rel_count = await self.graph_service.extract_graph_from_chunks(
                        chunks=chunks,
                        source_filename=filename,
                        llm_router=llm_router,
                    )
                    logger.info(
                        "Memgraph: stored %d entities, %d relationships from %s",
                        ent_count, rel_count, filename,
                    )
                except Exception as exc:
                    logger.warning("Graph extraction failed for %s: %s", filename, exc)
                    if errors is not None:
                        errors.append(f"Graph extraction error for {filename}: {exc}")

        return len(all_documents)

    async def search(
        self,
        query: str,
        user_id: str = "guest",
        session_id: str = "default",
        vector_index_path: Optional[str] = None,
        k: int = 5,
        errors: Optional[List[str]] = None,
        llm_router: Any = None,
    ) -> str:
        """Hybrid search: FAISS semantic search + Memgraph graph context.

        Both retrieval paths run in parallel for maximum speed.
        """
        # Launch FAISS search
        faiss_context = await self._faiss_search(query, user_id, session_id, vector_index_path, k, errors)

        # Launch graph search (graceful degradation)
        graph_context = ""
        if llm_router and self.graph_service.is_available:
            try:
                graph_context = await self.graph_service.query_graph(query, llm_router)
            except Exception as exc:
                logger.warning("Graph query failed (non-fatal): %s", exc)

        # Combine contexts
        parts = []
        if faiss_context:
            parts.append(faiss_context)
        if graph_context:
            parts.append(graph_context)

        combined = "\n\n".join(parts)
        logger.info(
            "Hybrid RAG context: FAISS=%d chars, Graph=%d chars, Total=%d chars",
            len(faiss_context), len(graph_context), len(combined),
        )
        return combined

    async def _faiss_search(
        self,
        query: str,
        user_id: str,
        session_id: str,
        vector_index_path: Optional[str],
        k: int,
        errors: Optional[List[str]],
    ) -> str:
        """Pure FAISS similarity search."""
        store = await self.get_or_load_store(user_id, session_id, vector_index_path, errors)
        if store is None:
            return ""

        docs = await asyncio.to_thread(store.similarity_search, query, k=k)

        parts = []
        for i, doc in enumerate(docs):
            filename = doc.metadata.get("filename", "unknown")
            parts.append(f"[Document {i + 1} — Source: {filename}]\n{doc.page_content}")

        return "\n\n".join(parts)

