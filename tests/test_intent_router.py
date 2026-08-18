"""Tests for the IntentRouter — classification, routing, edge cases."""

import json
import pytest
from unittest.mock import MagicMock, patch
from state.state import Intent
from state.normalize import normalize_state
from tests.conftest import MockLLMRouter, MockLLMResponse, make_base_state


class TestIntentClassification:
    """Verify intent classification logic."""

    def _make_router_with_response(self, intent: str, confidence: float = 0.9):
        """Helper: create IntentRouter with mocked LLM returning given intent."""
        response_json = json.dumps({
            "intent": intent,
            "confidence": confidence,
            "reasoning": f"Test classification as {intent}",
        })
        mock_router = MockLLMRouter(response_content=response_json)
        from agents.intent_router import IntentRouter
        return IntentRouter(llm_router=mock_router)

    def test_classify_rag_intent(self):
        router = self._make_router_with_response("rag", 0.95)
        state = normalize_state(make_base_state(user_query="Summarize the uploaded document"))
        result = router.classify(state)
        assert result["intent"] == "rag"
        assert result["intent_confidence"] == 0.95

    def test_classify_sql_intent(self):
        router = self._make_router_with_response("sql", 0.88)
        state = normalize_state(make_base_state(user_query="Show total sales by region"))
        result = router.classify(state)
        assert result["intent"] == "sql"

    def test_classify_code_intent(self):
        router = self._make_router_with_response("code", 0.92)
        state = normalize_state(make_base_state(user_query="Calculate fibonacci of 10"))
        result = router.classify(state)
        assert result["intent"] == "code"

    def test_classify_research_intent(self):
        router = self._make_router_with_response("research", 0.85)
        state = normalize_state(make_base_state(user_query="What is the weather in Delhi?"))
        result = router.classify(state)
        assert result["intent"] == "research"

    def test_classify_chat_intent(self):
        router = self._make_router_with_response("chat", 0.80)
        state = normalize_state(make_base_state(user_query="Tell me a joke"))
        result = router.classify(state)
        assert result["intent"] == "chat"

    def test_empty_query_returns_unknown(self):
        router = self._make_router_with_response("chat")
        state = normalize_state(make_base_state(user_query=""))
        result = router.classify(state)
        assert result["intent"] == Intent.UNKNOWN.value
        assert result["intent_confidence"] == 0.0

    def test_invalid_intent_from_llm_returns_unknown(self):
        """If LLM returns an invalid intent string, it should fallback to UNKNOWN."""
        router = self._make_router_with_response("invalid_agent", 0.99)
        state = normalize_state(make_base_state(user_query="Do something"))
        result = router.classify(state)
        assert result["intent"] == Intent.UNKNOWN.value

    def test_llm_error_does_not_crash(self):
        """IntentRouter.classify should NEVER raise."""
        mock_router = MagicMock()
        mock_router.invoke.side_effect = RuntimeError("LLM is down!")
        
        from agents.intent_router import IntentRouter
        router = IntentRouter(llm_router=mock_router)
        state = normalize_state(make_base_state(user_query="Test query"))
        
        # Should not raise
        result = router.classify(state)
        assert result["intent"] == Intent.UNKNOWN.value
        assert len(result["errors"]) > 0

    def test_malformed_json_response(self):
        """If LLM returns non-JSON, should still set some intent."""
        mock_router = MockLLMRouter(response_content="This is not JSON at all")
        from agents.intent_router import IntentRouter
        router = IntentRouter(llm_router=mock_router)
        state = normalize_state(make_base_state(user_query="Test query"))
        result = router.classify(state)
        # The fallback parser returns {"intent": "rag"} on parse failure
        assert result["intent"] in {i.value for i in Intent}


class TestIntentRouterRoute:
    """Verify the route() method returns correct node names."""

    def test_route_all_intents(self):
        from agents.intent_router import IntentRouter
        router = IntentRouter(llm_router=MockLLMRouter())
        
        mapping = {
            "rag": "rag_agent",
            "sql": "sql_agent",
            "code": "code_agent",
            "research": "research_agent",
            "chat": "chat_agent",
            "unknown": "chat_agent",  # UNKNOWN defaults to chat
        }
        
        for intent, expected_node in mapping.items():
            state = {"intent": intent}
            assert router.route(state) == expected_node, f"Failed for intent={intent}"

    def test_route_missing_intent_defaults_to_chat(self):
        from agents.intent_router import IntentRouter
        router = IntentRouter(llm_router=MockLLMRouter())
        assert router.route({}) == "chat_agent"


class TestSpecialCharactersInQuery:
    """Ensure curly braces and special chars don't break prompt templates."""

    def test_query_with_curly_braces(self):
        response_json = json.dumps({"intent": "chat", "confidence": 0.8, "reasoning": "test"})
        mock_router = MockLLMRouter(response_content=response_json)
        from agents.intent_router import IntentRouter
        router = IntentRouter(llm_router=mock_router)
        
        state = normalize_state(make_base_state(
            user_query="What is {this} and {{that}}?"
        ))
        # Should not crash with KeyError from prompt template
        result = router.classify(state)
        assert result["intent"] in {i.value for i in Intent}
