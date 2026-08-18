"""Tests for state normalization and invariant helpers."""

import pytest
from state.state import OrchestratorState, Intent, ExecutionStatus
from state.normalize import normalize_state, ensure_intent, ensure_metadata, ensure_errors


class TestNormalizeState:
    """Verify normalize_state guarantees all keys with safe defaults."""

    def test_empty_dict_returns_all_keys(self):
        """An empty dict should get ALL required keys with defaults."""
        result = normalize_state({})
        
        required_keys = [
            "tenant_id", "user_id", "is_guest", "session_id", "user_query",
            "intent", "intent_confidence", "messages", "conversation_turn",
            "chat_history", "memory_loaded_count", "uploaded_docs",
            "db_connection", "db_schema", "workspace_id", "uploaded_doc_ids",
            "workspace_documents", "vector_index_path", "retrieved_context",
            "generated_sql", "sql_validation_result", "code_to_execute",
            "execution_result", "tool_outputs", "research_results", "errors",
            "retry_count", "max_retries", "approved", "approval_required",
            "approval_reason", "execution_status", "confidence_score",
            "risk_level", "next_node", "should_continue", "requires_human_input",
            "final_answer", "metadata", "model_used", "fallback_reason",
        ]
        
        for key in required_keys:
            assert key in result, f"Missing key: {key}"

    def test_defaults_are_correct_types(self):
        result = normalize_state({})
        
        assert result["tenant_id"] == "default"
        assert result["user_id"] == "guest"
        assert result["is_guest"] is True  # guest user => is_guest = True
        assert result["intent"] == "unknown"
        assert isinstance(result["messages"], list)
        assert isinstance(result["errors"], list)
        assert isinstance(result["metadata"], dict)
        assert isinstance(result["tool_outputs"], dict)
        assert result["retry_count"] == 0
        assert result["max_retries"] == 3
        assert result["approved"] is False

    def test_preserves_existing_values(self):
        """normalize_state should NOT overwrite existing values."""
        state = {
            "tenant_id": "my-tenant",
            "user_query": "What is AI?",
            "intent": "rag",
        }
        result = normalize_state(state)
        assert result["tenant_id"] == "my-tenant"
        assert result["user_query"] == "What is AI?"
        assert result["intent"] == "rag"

    def test_fixes_non_list_messages(self):
        result = normalize_state({"messages": "not-a-list"})
        assert isinstance(result["messages"], list)
        assert result["messages"] == []

    def test_fixes_non_list_errors(self):
        result = normalize_state({"errors": None})
        assert isinstance(result["errors"], list)

    def test_fixes_non_dict_metadata(self):
        result = normalize_state({"metadata": "bad"})
        assert isinstance(result["metadata"], dict)

    def test_fixes_non_dict_tool_outputs(self):
        result = normalize_state({"tool_outputs": []})
        assert isinstance(result["tool_outputs"], dict)

    def test_is_guest_auto_detection(self):
        result = normalize_state({"user_id": "guest"})
        assert result["is_guest"] is True
        
        result2 = normalize_state({"user_id": "harsh"})
        assert result2["is_guest"] is False


class TestEnsureIntent:
    """Verify ensure_intent invariant helper."""

    def test_missing_intent_gets_default(self):
        state = {}
        ensure_intent(state)
        assert state["intent"] == Intent.UNKNOWN.value
        assert state["intent_confidence"] == 0.0

    def test_none_intent_gets_default(self):
        state = {"intent": None}
        ensure_intent(state)
        assert state["intent"] == Intent.UNKNOWN.value

    def test_existing_intent_preserved(self):
        state = {"intent": "rag", "intent_confidence": 0.9}
        ensure_intent(state)
        assert state["intent"] == "rag"
        assert state["intent_confidence"] == 0.9


class TestEnsureMetadata:
    def test_missing_metadata(self):
        state = {}
        ensure_metadata(state)
        assert state["metadata"] == {}

    def test_non_dict_metadata(self):
        state = {"metadata": "bad"}
        ensure_metadata(state)
        assert state["metadata"] == {}

    def test_existing_metadata_preserved(self):
        state = {"metadata": {"key": "value"}}
        ensure_metadata(state)
        assert state["metadata"] == {"key": "value"}


class TestEnsureErrors:
    def test_missing_errors(self):
        state = {}
        ensure_errors(state)
        assert state["errors"] == []

    def test_non_list_errors(self):
        state = {"errors": "crash"}
        ensure_errors(state)
        assert state["errors"] == []

    def test_existing_errors_preserved(self):
        state = {"errors": ["err1"]}
        ensure_errors(state)
        assert state["errors"] == ["err1"]


class TestIntentEnum:
    def test_all_intents_have_values(self):
        expected = {"rag", "sql", "code", "research", "chat", "unknown"}
        actual = {i.value for i in Intent}
        assert actual == expected

    def test_intent_is_string_enum(self):
        assert isinstance(Intent.RAG, str)
        assert Intent.RAG == "rag"
