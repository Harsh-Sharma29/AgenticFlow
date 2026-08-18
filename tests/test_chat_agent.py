"""Tests for Chat Agent."""

import pytest
from state.normalize import normalize_state
from tests.conftest import MockLLMRouter, make_base_state


class TestChatAgent:
    """Test chat agent execution."""

    def test_execute_returns_answer(self):
        mock_router = MockLLMRouter(response_content="I'm doing great, thank you!")
        from agents.chat_agent import ChatAgent
        agent = ChatAgent(llm_router=mock_router)
        
        state = normalize_state(make_base_state(user_query="How are you?"))
        result = agent.execute(state)
        
        assert result["final_answer"] == "I'm doing great, thank you!"
        assert result["execution_status"] == "completed"
        assert result["confidence_score"] == 0.8

    def test_execute_adds_to_messages(self):
        mock_router = MockLLMRouter(response_content="Hello there!")
        from agents.chat_agent import ChatAgent
        agent = ChatAgent(llm_router=mock_router)
        
        state = normalize_state(make_base_state(user_query="Hi"))
        result = agent.execute(state)
        
        # Should have added assistant message
        assistant_msgs = [m for m in result["messages"] if m["role"] == "assistant"]
        assert len(assistant_msgs) >= 1
        assert assistant_msgs[-1]["content"] == "Hello there!"
        assert assistant_msgs[-1]["metadata"]["agent"] == "chat"

    def test_execute_with_history(self):
        mock_router = MockLLMRouter(response_content="Your name is Harsh!")
        from agents.chat_agent import ChatAgent
        agent = ChatAgent(llm_router=mock_router)
        
        state = normalize_state(make_base_state(
            user_query="What is my name?",
            messages=[
                {"role": "user", "content": "My name is Harsh"},
                {"role": "assistant", "content": "Nice to meet you, Harsh!"},
            ]
        ))
        result = agent.execute(state)
        assert result["final_answer"] is not None

    def test_execute_error_handling(self):
        from unittest.mock import MagicMock
        mock_router = MagicMock()
        mock_router.invoke.side_effect = Exception("Connection failed")
        
        from agents.chat_agent import ChatAgent
        agent = ChatAgent(llm_router=mock_router)
        
        state = normalize_state(make_base_state(user_query="Test"))
        result = agent.execute(state)
        
        assert result["execution_status"] == "failed"
        assert len(result["errors"]) > 0
        assert result["final_answer"] is not None  # Should have fallback message
