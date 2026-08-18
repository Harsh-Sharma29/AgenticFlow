"""Shared test fixtures for the Nexus AI Orchestrator test suite.

All tests use mocked LLM/embedding calls — no real API keys needed.
"""

import os
import sys
import tempfile
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from typing import Dict, Any

# Ensure project root is on sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ── Mock LLM Response ──────────────────────────────────────────────

class MockLLMResponse:
    """Simulates a LangChain LLM response object."""
    def __init__(self, content: str = "This is a mock response."):
        self.content = content

    def __str__(self):
        return self.content


# ── Mock LLM Router ────────────────────────────────────────────────

class MockLLMRouter:
    """Mock LLM router that returns canned responses without API calls."""

    def __init__(self, response_content: str = "Mock LLM answer."):
        self.response_content = response_content
        self.invoke_count = 0
        self.last_messages = None
        self.last_kwargs = None

    def invoke(self, messages, *, state=None, **kwargs):
        self.invoke_count += 1
        self.last_messages = messages
        self.last_kwargs = kwargs
        if state is not None:
            state["model_used"] = "mock-model"
            state["fallback_reason"] = None
        return MockLLMResponse(self.response_content)


# ── Mock Embeddings ────────────────────────────────────────────────

class MockEmbeddings:
    """Mock embedding model that returns fixed-length vectors."""

    def embed_documents(self, texts):
        return [[0.1] * 768 for _ in texts]

    def embed_query(self, text):
        return [0.1] * 768


# ── State Factory ──────────────────────────────────────────────────

def make_base_state(**overrides) -> Dict[str, Any]:
    """Create a minimal valid OrchestratorState dict for testing."""
    state = {
        "tenant_id": "test-tenant",
        "user_id": "test-user",
        "is_guest": False,
        "session_id": "test-session-001",
        "user_query": "Hello, how are you?",
        "intent": None,
        "intent_confidence": 0.0,
        "messages": [],
        "conversation_turn": 0,
        "chat_history": [],
        "memory_loaded_count": 0,
        "uploaded_docs": [],
        "db_connection": None,
        "db_schema": None,
        "workspace_id": "default",
        "uploaded_doc_ids": [],
        "workspace_documents": [],
        "vector_index_path": None,
        "retrieved_context": None,
        "generated_sql": None,
        "sql_validation_result": None,
        "code_to_execute": None,
        "execution_result": None,
        "tool_outputs": {},
        "research_results": None,
        "errors": [],
        "retry_count": 0,
        "max_retries": 3,
        "approved": False,
        "approval_required": False,
        "approval_reason": None,
        "execution_status": "pending",
        "confidence_score": 0.0,
        "risk_level": None,
        "next_node": None,
        "should_continue": False,
        "requires_human_input": False,
        "final_answer": None,
        "metadata": {},
        "model_used": None,
        "fallback_reason": None,
    }
    state.update(overrides)
    return state


# ── Pytest Fixtures ────────────────────────────────────────────────

@pytest.fixture
def base_state():
    """Return a clean base state dict."""
    return make_base_state()


@pytest.fixture
def mock_llm_router():
    """Return a MockLLMRouter instance."""
    return MockLLMRouter()


@pytest.fixture
def mock_embeddings():
    """Return a MockEmbeddings instance."""
    return MockEmbeddings()


@pytest.fixture
def temp_db(tmp_path):
    """Return a temporary SQLite DB path for storage tests."""
    return str(tmp_path / "test_memory.db")


@pytest.fixture
def sample_text_file(tmp_path):
    """Create a sample text file for RAG testing."""
    doc_path = tmp_path / "sample_doc.txt"
    doc_path.write_text(
        "Nexus AI Orchestrator is an enterprise-grade multi-agent system. "
        "It uses LangGraph for workflow management and supports RAG, SQL, "
        "Code execution, and Research agents. The system features "
        "tenant-scoped isolation and quota-aware LLM routing with "
        "Gemini as the primary model and HuggingFace as fallback."
    )
    return str(doc_path)
