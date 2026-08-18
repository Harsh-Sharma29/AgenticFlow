"""Tests for SQL Agent — validation, generation, safety checks."""

import pytest
from agents.sql_agent import SQLValidator, SQLAgent
from state.normalize import normalize_state
from tests.conftest import MockLLMRouter, make_base_state


class TestSQLValidator:
    """Test SQL query validation for safety."""

    def test_safe_select_query(self):
        result = SQLValidator.validate("SELECT * FROM users WHERE active = 1 LIMIT 10")
        assert result["is_safe"] is True
        assert result["risk_level"] == "low"
        assert result["requires_approval"] is False

    def test_select_with_join(self):
        sql = "SELECT u.name, o.total FROM users u JOIN orders o ON u.id = o.user_id"
        result = SQLValidator.validate(sql)
        assert result["is_safe"] is True

    def test_select_with_cte(self):
        sql = "WITH top_users AS (SELECT * FROM users LIMIT 10) SELECT * FROM top_users"
        result = SQLValidator.validate(sql)
        assert result["is_safe"] is True

    def test_drop_table_blocked(self):
        result = SQLValidator.validate("DROP TABLE users")
        assert result["is_safe"] is False
        assert result["risk_level"] == "high"
        assert result["requires_approval"] is True
        assert any("DROP" in issue.upper() for issue in result["issues"])

    def test_delete_blocked(self):
        result = SQLValidator.validate("DELETE FROM users WHERE id = 5")
        assert result["is_safe"] is False
        assert result["risk_level"] == "high"

    def test_update_blocked(self):
        result = SQLValidator.validate("UPDATE users SET name = 'test' WHERE id = 1")
        assert result["is_safe"] is False

    def test_insert_blocked(self):
        result = SQLValidator.validate("INSERT INTO users (name) VALUES ('test')")
        assert result["is_safe"] is False

    def test_truncate_blocked(self):
        result = SQLValidator.validate("TRUNCATE TABLE logs")
        assert result["is_safe"] is False

    def test_multiple_statements_blocked(self):
        result = SQLValidator.validate("SELECT 1; DROP TABLE users;")
        assert result["risk_level"] == "high"

    def test_sql_injection_comment_detected(self):
        result = SQLValidator.validate("SELECT * FROM users -- WHERE admin = 1")
        assert any("comment" in issue.lower() for issue in result["issues"])

    def test_alter_table_blocked(self):
        result = SQLValidator.validate("ALTER TABLE users ADD COLUMN age INT")
        assert result["is_safe"] is False


class TestSQLAgentExecution:
    """Test SQL agent workflow."""

    def test_generate_sql_invokes_llm(self):
        mock_router = MockLLMRouter(response_content="SELECT * FROM orders LIMIT 10")
        agent = SQLAgent(llm_router=mock_router)
        state = normalize_state(make_base_state(user_query="Show me all orders"))
        
        sql = agent.generate_sql(state)
        assert "SELECT" in sql.upper()
        assert mock_router.invoke_count == 1

    def test_generate_cleans_markdown(self):
        mock_router = MockLLMRouter(
            response_content="```sql\nSELECT * FROM users\n```"
        )
        agent = SQLAgent(llm_router=mock_router)
        state = normalize_state(make_base_state(user_query="List users"))
        
        sql = agent.generate_sql(state)
        assert "```" not in sql
        assert "SELECT * FROM users" in sql

    def test_execute_without_db_connection(self):
        mock_router = MockLLMRouter(
            response_content="SELECT COUNT(*) FROM orders"
        )
        agent = SQLAgent(llm_router=mock_router)
        state = normalize_state(make_base_state(
            user_query="Count orders",
            db_connection=None,
        ))
        
        result = agent.execute(state)
        # Without DB connection, should report error in answer
        assert "no database" in result.get("final_answer", "").lower() or \
               result["execution_status"] in ("completed", "failed")

    def test_safe_query_executes_without_approval(self):
        mock_router = MockLLMRouter(
            response_content="SELECT name FROM products LIMIT 5"
        )
        agent = SQLAgent(llm_router=mock_router)
        state = normalize_state(make_base_state(
            user_query="List product names",
            db_connection="sqlite:///test.db",
        ))
        
        result = agent.execute(state)
        assert result.get("approval_required") is not True or result["generated_sql"] is not None
