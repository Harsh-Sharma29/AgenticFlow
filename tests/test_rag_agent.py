"""Tests for the RAG Agent — placeholder bug, retrieval, document loading."""

import os
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from state.normalize import normalize_state
from tests.conftest import MockLLMRouter, MockEmbeddings, make_base_state


class TestRAGPlaceholderBug:
    """Verify the placeholder text 'No documents loaded yet.' is properly caught."""

    def _make_rag_agent(self, mock_router=None):
        """Create RAG agent with mocked embeddings."""
        with patch("agents.rag_agent.GoogleGenerativeAIEmbeddings"):
            from agents.rag_agent import RAGAgent
            agent = RAGAgent(llm_router=mock_router or MockLLMRouter())
            agent._embeddings = MockEmbeddings()
            return agent

    def test_empty_store_returns_no_content_message(self):
        """When no docs uploaded, RAG should NOT hallucinate from placeholder."""
        agent = self._make_rag_agent()
        state = normalize_state(make_base_state(
            user_query="What is in the document?"
        ))
        
        # Execute — should detect empty store and return helpful message
        with patch.object(agent, '_get_workspace_store') as mock_store:
            # Simulate FAISS returning the placeholder text with low relevance
            from langchain_core.documents import Document
            placeholder_doc = Document(page_content="No documents loaded yet.")
            mock_store.return_value.similarity_search_with_score.return_value = [
                (placeholder_doc, 0.5)
            ]
            
            context = agent.retrieve(state)
            # Placeholder should be filtered out — return value is empty
            assert context == ""

    def test_generate_answer_catches_empty_context(self):
        """generate_answer should return helpful message when context is empty."""
        agent = self._make_rag_agent()
        state = normalize_state(make_base_state(
            user_query="What does the document say?"
        ))
        
        with patch.object(agent, 'retrieve', return_value=""):
            answer = agent.generate_answer(state)
            assert "do not contain" in answer.lower() or "upload" in answer.lower()


class TestRAGRetrieval:
    """Test retrieval with score-based filtering."""

    def _make_rag_agent(self):
        with patch("agents.rag_agent.GoogleGenerativeAIEmbeddings"):
            from agents.rag_agent import RAGAgent
            agent = RAGAgent(llm_router=MockLLMRouter())
            agent._embeddings = MockEmbeddings()
            return agent

    def test_high_score_chunks_filtered_out(self):
        """Chunks with score > threshold should be excluded."""
        agent = self._make_rag_agent()
        state = normalize_state(make_base_state(user_query="test query"))
        
        from langchain_core.documents import Document
        with patch.object(agent, '_get_workspace_store') as mock_store:
            mock_store.return_value.similarity_search_with_score.return_value = [
                (Document(page_content="Relevant content"), 0.3),  # Good
                (Document(page_content="Irrelevant noise"), 2.0),  # Bad — above threshold
            ]
            
            context = agent.retrieve(state, score_threshold=1.2)
            assert "Relevant content" in context
            assert "Irrelevant noise" not in context

    def test_all_relevant_chunks_kept(self):
        """Chunks below threshold should all be included."""
        agent = self._make_rag_agent()
        state = normalize_state(make_base_state(user_query="test query"))
        
        from langchain_core.documents import Document
        with patch.object(agent, '_get_workspace_store') as mock_store:
            mock_store.return_value.similarity_search_with_score.return_value = [
                (Document(page_content="Chunk A"), 0.2),
                (Document(page_content="Chunk B"), 0.5),
                (Document(page_content="Chunk C"), 0.8),
            ]
            
            context = agent.retrieve(state, score_threshold=1.2)
            assert "Chunk A" in context
            assert "Chunk B" in context
            assert "Chunk C" in context


class TestRAGDocumentLoading:
    """Test document loading for various file types."""

    def _make_rag_agent(self):
        with patch("agents.rag_agent.GoogleGenerativeAIEmbeddings"):
            from agents.rag_agent import RAGAgent
            agent = RAGAgent(llm_router=MockLLMRouter())
            agent._embeddings = MockEmbeddings()
            return agent

    def test_load_text_file(self, sample_text_file):
        """Should load .txt files without errors."""
        agent = self._make_rag_agent()
        state = normalize_state(make_base_state(
            uploaded_docs=[sample_text_file]
        ))
        
        with patch.object(agent, '_get_workspace_store') as mock_store:
            mock_store.return_value.add_documents = MagicMock()
            agent.load_documents(state)
            # add_documents should have been called with split chunks
            mock_store.return_value.add_documents.assert_called_once()

    def test_missing_file_records_error(self):
        """Loading a non-existent file should record an error, not crash."""
        agent = self._make_rag_agent()
        state = normalize_state(make_base_state(
            uploaded_docs=["/fake/path/nonexistent.txt"]
        ))
        
        agent.load_documents(state)
        assert any("not found" in e.lower() for e in state["errors"])

    def test_unsupported_file_type(self, tmp_path):
        """Unsupported file types should be skipped with error."""
        unsupported = tmp_path / "data.xlsx"
        unsupported.write_text("fake")
        
        agent = self._make_rag_agent()
        state = normalize_state(make_base_state(
            uploaded_docs=[str(unsupported)]
        ))
        
        agent.load_documents(state)
        assert any("unsupported" in e.lower() for e in state["errors"])

    def test_empty_doc_list_is_noop(self):
        """Empty doc list should not trigger any loading."""
        agent = self._make_rag_agent()
        state = normalize_state(make_base_state(uploaded_docs=[]))
        
        agent.load_documents(state)
        assert state["errors"] == []


class TestRAGExecute:
    """Test the full execute() pipeline."""

    def _make_rag_agent(self, answer="Here is the answer from docs."):
        with patch("agents.rag_agent.GoogleGenerativeAIEmbeddings"):
            from agents.rag_agent import RAGAgent
            agent = RAGAgent(llm_router=MockLLMRouter(response_content=answer))
            agent._embeddings = MockEmbeddings()
            return agent

    def test_execute_sets_final_answer(self):
        agent = self._make_rag_agent()
        state = normalize_state(make_base_state(user_query="What is in the doc?"))
        
        # Mock retrieve to return valid context
        with patch.object(agent, 'retrieve', return_value="Document content about AI orchestration"):
            result = agent.execute(state)
            assert result["final_answer"] is not None
            assert result["execution_status"] == "completed"

    def test_execute_error_handling(self):
        agent = self._make_rag_agent()
        state = normalize_state(make_base_state(user_query="Crash test"))
        
        with patch.object(agent, 'generate_answer', side_effect=Exception("Boom!")):
            result = agent.execute(state)
            assert result["execution_status"] == "failed"
            assert len(result["errors"]) > 0
