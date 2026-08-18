"""Tests for LLM Router — primary model, fallback, state tracking."""

import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from tests.conftest import MockLLMResponse


class TestLLMRouter:
    """Test LLM routing and fallback logic."""

    def _make_router(self, **kwargs):
        from llm.router import LLMRouter
        return LLMRouter(**kwargs)

    def test_primary_model_invocation(self):
        router = self._make_router()
        
        mock_primary = MagicMock()
        mock_primary.invoke.return_value = MockLLMResponse("Primary answer")
        router._primary = mock_primary
        
        from langchain_core.messages import HumanMessage
        messages = [HumanMessage(content="Test")]
        
        state = {}
        resp = router.invoke(messages, state=state)
        
        assert resp.content == "Primary answer"
        assert state["model_used"] == router.primary_model
        assert state["fallback_reason"] is None

    def test_fallback_on_429(self):
        router = self._make_router(enable_fallback=True)
        
        # Primary throws 429
        mock_primary = MagicMock()
        mock_primary.invoke.side_effect = Exception("429 ResourceExhausted quota exceeded")
        router._primary = mock_primary
        
        # Fallback works
        mock_fallback = MagicMock()
        mock_fallback.invoke.return_value = MockLLMResponse("Fallback answer")
        router._fallback = mock_fallback
        
        from langchain_core.messages import HumanMessage
        messages = [HumanMessage(content="Test")]
        
        state = {}
        resp = router.invoke(messages, state=state)
        
        assert resp.content == "Fallback answer"
        assert "huggingface" in state["model_used"]
        assert state["fallback_reason"] is not None
        assert "429" in state["fallback_reason"]

    def test_non_429_error_propagates(self):
        router = self._make_router(enable_fallback=True)
        
        mock_primary = MagicMock()
        mock_primary.invoke.side_effect = ValueError("Invalid API key")
        router._primary = mock_primary
        
        from langchain_core.messages import HumanMessage
        messages = [HumanMessage(content="Test")]
        
        with pytest.raises(ValueError, match="Invalid API key"):
            router.invoke(messages)

    def test_fallback_disabled(self):
        router = self._make_router(enable_fallback=False)
        
        mock_primary = MagicMock()
        mock_primary.invoke.side_effect = Exception("429 ResourceExhausted")
        router._primary = mock_primary
        
        from langchain_core.messages import HumanMessage
        messages = [HumanMessage(content="Test")]
        
        # With fallback disabled, even 429 should propagate
        with pytest.raises(Exception):
            router.invoke(messages)


class TestQuotaDetection:
    """Test _is_quota_exhausted_429 method."""

    def _make_router(self):
        from llm.router import LLMRouter
        return LLMRouter()

    def test_detects_resource_exhausted_429(self):
        router = self._make_router()
        
        assert router._is_quota_exhausted_429(Exception("429 ResourceExhausted")) is True
        assert router._is_quota_exhausted_429(Exception("resource exhausted 429")) is True
        assert router._is_quota_exhausted_429(Exception("429 quota limit")) is True

    def test_does_not_match_other_errors(self):
        router = self._make_router()
        
        assert router._is_quota_exhausted_429(Exception("Invalid API key")) is False
        assert router._is_quota_exhausted_429(Exception("500 Internal Server Error")) is False
        assert router._is_quota_exhausted_429(Exception("Connection timeout")) is False

    def test_does_not_match_429_without_quota(self):
        router = self._make_router()
        # Just "429" alone without quota/resource context
        assert router._is_quota_exhausted_429(Exception("Error 429")) is False
