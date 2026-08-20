"""Asynchronous Hybrid RAG (Retrieval-Augmented Generation) service.

Combines PGVector vector-store (semantic search) with Memgraph knowledge-graph
(relationship search) for production-grade GraphRAG. All public methods are
``async def`` so they integrate cleanly with the async LangGraph nodes.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any, Dict, List, Optional

from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_postgres import PGVector

from backend.app.config import get_settings
from backend.app.services.local_ingestion import LocalDocumentIngestor
from backend.app.services.graph_service import MemgraphService
from backend.app.utils.filenames import resolve_workspace_doc_path

logger = logging.getLogger(__name__)

COLLECTION_NAME = "nexus_docs"


class RAGService:
    """Async-ready Hybrid RAG service: PGVector + Memgraph (graph)."""

    def __init__(
        self,
        embedding_model: str = "BAAI/bge-small-en-v1.5",
        chunk_size: int = 800,
        chunk_overlap: int = 100,
    ) -> None:
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self._embedding_model_name = embedding_model

        # Local CPU embedding model
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
            
        # PGVector client
        db_url = get_settings().POSTGRES_URI
        if db_url.startswith("postgresql://"):
            self.connection = db_url.replace("postgresql://", "postgresql+psycopg2://")
        else:
            self.connection = db_url
            
        self.vector_store = None

    @property
    def embeddings(self) -> HuggingFaceEmbeddings:
        """Lazily initialise the local HuggingFace embedding model."""
        if self._embeddings is None:
            logger.info("Initializing HuggingFaceEmbeddings: %s", self._embedding_model_name)
            self._embeddings = HuggingFaceEmbeddings(
                model_name=self._embedding_model_name,
                model_kwargs={"device": "cpu"},
                encode_kwargs={"normalize_embeddings": True, "batch_size": 32},
            )
        return self._embeddings

    def _ensure_collection(self):
        """Ensure PGVector collection exists."""
        if self.vector_store is not None:
            return
            
        try:
            self.vector_store = PGVector(
                embeddings=self.embeddings,
                collection_name=COLLECTION_NAME,
                connection=self.connection,
                use_jsonb=True,
            )
        except Exception as e:
            logger.error("Failed to initialize PGVector store: %s", e)

    async def load_documents(
        self,
        doc_paths: List[str],
        user_id: str = "guest",
        session_id: str = "default",
        vector_index_path: Optional[str] = None, # Kept for signature compatibility
        errors: Optional[List[str]] = None,
        llm_router: Any = None,
    ) -> int:
        """Load, chunk, embed and index documents into PGVector + Memgraph."""
        if not doc_paths:
            return 0
            
        self._ensure_collection()

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
                            metadata={
                                "source": doc_path, 
                                "filename": filename,
                                "user_id": user_id,
                                "session_id": session_id
                            },
                        )
                    )

            except Exception as exc:
                if errors is not None:
                    errors.append(f"Error loading {doc_path}: {exc}")
                logger.exception("Error loading document %s", doc_path)

        if not all_documents:
            return 0

        # ── PGVector indexing ──────────────────────────────────────────────
        logger.info("Embedding and indexing %d chunks into PGVector...", len(all_documents))
        if self.vector_store:
            try:
                await asyncio.to_thread(self.vector_store.add_documents, all_documents)
            except Exception as e:
                logger.error("PGVector index failed: %s", e)
                if errors is not None:
                    errors.append(f"PGVector index failed: {e}")

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
        vector_index_path: Optional[str] = None, # Kept for signature compatibility
        k: int = 5,
        errors: Optional[List[str]] = None,
        llm_router: Any = None,
    ) -> str:
        """Hybrid search: PGVector semantic search + Memgraph graph context."""
        # Launch PGVector search
        vector_context = await self._vector_search(query, user_id, session_id, k, errors)

        # Launch graph search (graceful degradation)
        graph_context = ""
        if llm_router and self.graph_service.is_available:
            try:
                graph_context = await self.graph_service.query_graph(query, llm_router)
            except Exception as exc:
                logger.warning("Graph query failed (non-fatal): %s", exc)

        # Combine contexts
        parts = []
        if vector_context:
            parts.append(vector_context)
        if graph_context:
            parts.append(graph_context)

        combined = "\n\n".join(parts)
        logger.info(
            "Hybrid RAG context: PGVector=%d chars, Graph=%d chars, Total=%d chars",
            len(vector_context), len(graph_context), len(combined),
        )
        return combined

    async def _vector_search(
        self,
        query: str,
        user_id: str,
        session_id: str,
        k: int,
        errors: Optional[List[str]],
    ) -> str:
        """Pure PGVector similarity search with metadata filtering."""
        self._ensure_collection()
        if self.vector_store is None:
            return ""

        try:
            # Metadata filter to isolate user's documents
            search_filter = {
                "user_id": user_id,
                "session_id": session_id
            }
            
            docs = await asyncio.to_thread(
                self.vector_store.similarity_search,
                query=query,
                k=k,
                filter=search_filter
            )

            parts = []
            for i, doc in enumerate(docs):
                filename = doc.metadata.get("filename", "unknown")
                parts.append(f"[Document {i + 1} — Source: {filename}]\n{doc.page_content}")

            return "\n\n".join(parts)
        except Exception as e:
            logger.error("PGVector search error: %s", e)
            if errors is not None:
                errors.append(f"PGVector search error: {e}")
            return ""
