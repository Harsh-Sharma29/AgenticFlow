import logging
from typing import Any, Dict, List
import fitz  # PyMuPDF
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

class LocalDocumentIngestor:
    """Highly efficient, local RAG document ingestion pipeline."""

    def __init__(
        self,
        model_name: str = "BAAI/bge-small-en-v1.5",
        device: str = "cpu",
        chunk_size: int = 800,
        chunk_overlap: int = 100,
        batch_size: int = 32
    ) -> None:
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.batch_size = batch_size
        
        logger.info(f"Initializing local embedding model: {model_name} on {device}")
        self.embedder = SentenceTransformer(model_name, device=device)
        
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            length_function=len,
            is_separator_regex=False,
        )

    def extract_pdf_text(self, pdf_path: str) -> str:
        """Extract text from a PDF file using PyMuPDF."""
        logger.info(f"Extracting text from: {pdf_path}")
        text = ""
        try:
            with fitz.open(pdf_path) as doc:
                for page in doc:
                    text += page.get_text() + "\n"
        except Exception as e:
            logger.error(f"Failed to extract text from {pdf_path}: {e}")
            raise
        return text

    def chunk_text(self, text: str) -> List[str]:
        """Split text into overlapping chunks."""
        logger.info("Splitting text into chunks...")
        chunks = self.text_splitter.split_text(text)
        logger.info(f"Created {len(chunks)} chunks.")
        return chunks

    def generate_embeddings(self, chunks: List[str]) -> List[Dict[str, Any]]:
        """Generate embeddings in explicit batches to prevent memory crashes."""
        logger.info(f"Generating embeddings for {len(chunks)} chunks in batches of {self.batch_size}...")
        
        results = []
        for i in range(0, len(chunks), self.batch_size):
            batch_chunks = chunks[i: i + self.batch_size]
            logger.debug(f"Processing batch {i // self.batch_size + 1}/{(len(chunks) + self.batch_size - 1) // self.batch_size}")
            
            # encode produces a numpy array
            embeddings = self.embedder.encode(
                batch_chunks,
                batch_size=self.batch_size,
                normalize_embeddings=True,
                show_progress_bar=False
            )
            
            for j, (chunk_text, embedding) in enumerate(zip(batch_chunks, embeddings)):
                global_index = i + j
                results.append({
                    "index": global_index,
                    "text": chunk_text,
                    "embedding": embedding.tolist()  # Convert numpy array to list
                })
                
        logger.info("Successfully generated all embeddings.")
        return results

    def process_document(self, file_path: str) -> List[Dict[str, Any]]:
        """Complete pipeline: Extract -> Chunk -> Embed."""
        if file_path.lower().endswith('.pdf'):
            text = self.extract_pdf_text(file_path)
        else:
            with open(file_path, 'r', encoding='utf-8') as f:
                text = f.read()
                
        chunks = self.chunk_text(text)
        return self.generate_embeddings(chunks)
