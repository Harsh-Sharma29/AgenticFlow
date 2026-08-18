"""Tests for Research Agent."""

import pytest
from unittest.mock import patch, MagicMock
from state.normalize import normalize_state
from tests.conftest import MockLLMRouter, make_base_state


class TestResearchSearch:
    """Test search functionality."""

    def test_search_with_mock_ddg(self):
        from agents.research_agent import ResearchAgent
        agent = ResearchAgent(llm_router=MockLLMRouter())
        
        with patch.object(agent, '_search_tool', MagicMock()) as mock_ddg:
            mock_ddg.run.return_value = "Result 1: Delhi weather is 35°C\n\nResult 2: Humidity 80%"
            results = agent.search("Delhi weather")
            assert len(results) > 0
            assert any("35" in r.get("snippet", "") for r in results)

    def test_search_returns_empty_on_failure(self):
        from agents.research_agent import ResearchAgent
        agent = ResearchAgent(llm_router=MockLLMRouter())
        
        with patch.object(agent, '_search_tool', MagicMock()) as mock_ddg:
            mock_ddg.run.side_effect = Exception("Network error")
            results = agent.search("failing query")
            assert results == []

    def test_search_handles_no_tool(self):
        from agents.research_agent import ResearchAgent
        agent = ResearchAgent(llm_router=MockLLMRouter())
        agent._search_tool = False  # Marked as unavailable
        
        results = agent.search("anything")
        assert results == []


class TestResearchExecution:
    """Test full research agent workflow."""

    def test_execute_with_results(self):
        mock_router = MockLLMRouter(
            response_content="Delhi weather is currently 35°C with 80% humidity."
        )
        from agents.research_agent import ResearchAgent
        agent = ResearchAgent(llm_router=mock_router)
        
        state = normalize_state(make_base_state(user_query="Delhi weather"))
        
        with patch.object(agent, 'search', return_value=[
            {"title": "Weather", "snippet": "Delhi 35°C", "url": ""}
        ]):
            result = agent.execute(state)
            assert result["final_answer"] is not None
            assert result["execution_status"] == "completed"
            assert result["research_results"] is not None

    def test_execute_with_empty_results(self):
        """Should still generate an answer using general knowledge."""
        mock_router = MockLLMRouter(
            response_content="Based on general knowledge, Delhi is usually hot in summer."
        )
        from agents.research_agent import ResearchAgent
        agent = ResearchAgent(llm_router=mock_router)
        
        state = normalize_state(make_base_state(user_query="Delhi weather"))
        
        with patch.object(agent, 'search', return_value=[]):
            result = agent.execute(state)
            assert result["final_answer"] is not None
            assert result["execution_status"] == "completed"

    def test_execute_error_handling(self):
        from unittest.mock import MagicMock as Mock
        mock_router = Mock()
        mock_router.invoke.side_effect = Exception("LLM down")
        
        from agents.research_agent import ResearchAgent
        agent = ResearchAgent(llm_router=mock_router)
        
        state = normalize_state(make_base_state(user_query="Test"))
        
        with patch.object(agent, 'search', return_value=[]):
            result = agent.execute(state)
            assert result["execution_status"] == "failed"
            assert len(result["errors"]) > 0

    def test_adds_message_to_history(self):
        mock_router = MockLLMRouter(response_content="Answer here")
        from agents.research_agent import ResearchAgent
        agent = ResearchAgent(llm_router=mock_router)
        
        state = normalize_state(make_base_state(user_query="Search test"))
        
        with patch.object(agent, 'search', return_value=[]):
            result = agent.execute(state)
            assistant_msgs = [m for m in result["messages"] if m["role"] == "assistant"]
            assert len(assistant_msgs) >= 1
            assert assistant_msgs[-1]["metadata"]["agent"] == "research"
