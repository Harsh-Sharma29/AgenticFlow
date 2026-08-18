"""Tests for Orchestrator Graph — routing, compilation, end-to-end flow."""

import json
import pytest
from unittest.mock import patch, MagicMock
from state.state import Intent
from state.normalize import normalize_state
from tests.conftest import MockLLMRouter, MockLLMResponse, MockEmbeddings, make_base_state


class TestGraphRouting:
    """Test _route_after_classification logic.
    
    These tests create the orchestrator instance directly, patching the
    lazy-imported dependencies at their SOURCE modules (llm.router, etc.)
    rather than at orchestrator.graph (where they don't exist as attributes).
    """

    def _make_orchestrator(self):
        """Create orchestrator with all LLM/embedding calls mocked."""
        with patch("llm.router.LLMRouter", return_value=MockLLMRouter()), \
             patch("llm.router.ChatGoogleGenerativeAI"), \
             patch("llm.router.ChatHuggingFace"), \
             patch("llm.router.HuggingFaceEndpoint"), \
             patch("agents.rag_agent.GoogleGenerativeAIEmbeddings"), \
             patch("agents.research_agent.DuckDuckGoSearchRun", return_value=MagicMock()):
            from orchestrator.graph import AIOrchestrator
            orch = AIOrchestrator(enable_checkpointing=False)
            orch.rag_agent._embeddings = MockEmbeddings()
            return orch

    def test_rag_intent_routes_to_rag(self):
        orch = self._make_orchestrator()
        state = normalize_state(make_base_state(
            intent=Intent.RAG.value,
            intent_confidence=0.9,
        ))
        result = orch._route_after_classification(state)
        assert result == "rag_agent"

    def test_sql_intent_routes_to_sql(self):
        orch = self._make_orchestrator()
        state = normalize_state(make_base_state(
            intent=Intent.SQL.value,
            intent_confidence=0.9,
        ))
        result = orch._route_after_classification(state)
        assert result == "sql_agent"

    def test_code_intent_routes_to_code(self):
        orch = self._make_orchestrator()
        state = normalize_state(make_base_state(
            intent=Intent.CODE.value,
            intent_confidence=0.9,
        ))
        result = orch._route_after_classification(state)
        assert result == "code_agent"

    def test_research_intent_routes_to_research(self):
        orch = self._make_orchestrator()
        state = normalize_state(make_base_state(
            intent=Intent.RESEARCH.value,
            intent_confidence=0.9,
        ))
        result = orch._route_after_classification(state)
        assert result == "research_agent"

    def test_chat_intent_routes_to_chat(self):
        orch = self._make_orchestrator()
        state = normalize_state(make_base_state(
            intent=Intent.CHAT.value,
            intent_confidence=0.9,
        ))
        result = orch._route_after_classification(state)
        assert result == "chat_agent"

    def test_unknown_low_confidence_goes_to_fallback(self):
        orch = self._make_orchestrator()
        state = normalize_state(make_base_state(
            intent=Intent.UNKNOWN.value,
            intent_confidence=0.1,
            uploaded_docs=[],
            workspace_documents=[],
        ))
        result = orch._route_after_classification(state)
        assert result == "fallback_handler"

    def test_weather_query_goes_to_research(self):
        """Weather keyword should trigger research override."""
        orch = self._make_orchestrator()
        state = normalize_state(make_base_state(
            user_query="What is the weather in Mumbai?",
            intent=Intent.CHAT.value,
            intent_confidence=0.8,
        ))
        result = orch._route_after_classification(state)
        assert result == "research_agent"

    def test_document_query_does_not_incorrectly_go_to_research(self):
        """'current' keyword removed — should NOT trigger research for doc queries."""
        orch = self._make_orchestrator()
        state = normalize_state(make_base_state(
            user_query="What does the current document say about revenue?",
            intent=Intent.RAG.value,
            intent_confidence=0.85,
        ))
        result = orch._route_after_classification(state)
        # Should NOT be overridden to research
        assert result == "rag_agent"


class TestGraphCompilation:
    """Test that the graph compiles without errors."""

    def _patch_all(self):
        """Return a combined patch context for lazy imports."""
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch("llm.router.ChatGoogleGenerativeAI"))
        stack.enter_context(patch("llm.router.ChatHuggingFace"))
        stack.enter_context(patch("llm.router.HuggingFaceEndpoint"))
        stack.enter_context(patch("agents.rag_agent.GoogleGenerativeAIEmbeddings"))
        stack.enter_context(patch("agents.research_agent.DuckDuckGoSearchRun", return_value=MagicMock()))
        return stack

    def test_graph_compiles(self):
        with self._patch_all():
            from orchestrator.graph import AIOrchestrator
            orch = AIOrchestrator(enable_checkpointing=False)
            assert orch.app is not None

    def test_graph_has_all_nodes(self):
        with self._patch_all():
            from orchestrator.graph import AIOrchestrator
            orch = AIOrchestrator(enable_checkpointing=False)
            
            expected_nodes = [
                "load_persistent_context", "classify_intent",
                "rag_agent", "sql_agent", "code_agent",
                "research_agent", "chat_agent",
                "save_persistent_context", "approval_gate",
                "retry_handler", "graceful_fallback", "fallback_handler",
            ]
            graph_nodes = list(orch.graph.nodes.keys())
            for node in expected_nodes:
                assert node in graph_nodes, f"Missing node: {node}"


class TestRouteAfterAgent:
    """Test post-agent routing decisions."""

    def _make_orchestrator(self):
        with patch("llm.router.ChatGoogleGenerativeAI"), \
             patch("llm.router.ChatHuggingFace"), \
             patch("llm.router.HuggingFaceEndpoint"), \
             patch("agents.rag_agent.GoogleGenerativeAIEmbeddings"), \
             patch("agents.research_agent.DuckDuckGoSearchRun", return_value=MagicMock()):
            from orchestrator.graph import AIOrchestrator
            return AIOrchestrator(enable_checkpointing=False)

    def test_completed_agent_goes_to_end(self):
        orch = self._make_orchestrator()
        state = normalize_state(make_base_state(
            execution_status="completed",
            approval_required=False,
        ))
        result = orch._route_after_agent(state)
        assert result == "end"

    def test_approval_required_goes_to_gate(self):
        orch = self._make_orchestrator()
        state = normalize_state(make_base_state(
            approval_required=True,
            approved=False,
        ))
        result = orch._route_after_agent(state)
        assert result == "approval_gate"
