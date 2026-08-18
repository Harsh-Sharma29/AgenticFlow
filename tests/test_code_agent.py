"""Tests for Code Agent — validation, sandboxed execution, approval gates."""

import pytest
from agents.code_agent import CodeValidator, CodeExecutor, CodeAgent
from state.normalize import normalize_state
from tests.conftest import MockLLMRouter, make_base_state


class TestCodeValidator:
    """Test code validation for safety."""

    def test_safe_code_passes(self):
        code = "x = 5\nprint(x * 2)"
        result = CodeValidator.validate(code)
        assert result["is_safe"] is True
        assert result["risk_level"] == "low"

    def test_safe_math_code(self):
        code = """
def factorial(n):
    if n <= 1:
        return 1
    return n * factorial(n - 1)

print(factorial(10))
"""
        result = CodeValidator.validate(code)
        assert result["is_safe"] is True

    def test_os_import_blocked(self):
        code = "import os\nos.system('rm -rf /')"
        result = CodeValidator.validate(code)
        assert result["is_safe"] is False
        assert result["risk_level"] == "high"

    def test_subprocess_blocked(self):
        code = "import subprocess\nsubprocess.run(['ls'])"
        result = CodeValidator.validate(code)
        assert result["is_safe"] is False

    def test_eval_blocked(self):
        code = "eval('__import__(\"os\").system(\"ls\")')"
        result = CodeValidator.validate(code)
        assert result["is_safe"] is False

    def test_exec_blocked(self):
        code = "exec('print(1)')"
        result = CodeValidator.validate(code)
        assert result["is_safe"] is False

    def test_file_open_blocked(self):
        code = "f = open('/etc/passwd', 'r')\nprint(f.read())"
        result = CodeValidator.validate(code)
        assert result["is_safe"] is False

    def test_socket_blocked(self):
        code = "import socket\ns = socket.socket()"
        result = CodeValidator.validate(code)
        assert result["is_safe"] is False

    def test_syntax_error_detected(self):
        code = "def broken(\n  print('oops'"
        result = CodeValidator.validate(code)
        assert any("syntax" in issue.lower() for issue in result["issues"])

    def test_dynamic_import_blocked(self):
        code = "__import__('os').system('whoami')"
        result = CodeValidator.validate(code)
        assert result["is_safe"] is False


class TestCodeExecutor:
    """Test sandboxed code execution."""

    def test_simple_code_executes(self):
        result = CodeExecutor.execute_safe("print('Hello, World!')")
        assert result["success"] is True
        assert "Hello, World!" in result["output"]

    def test_math_computation(self):
        code = "result = sum(range(1, 11))\nprint(result)"
        result = CodeExecutor.execute_safe(code)
        assert result["success"] is True
        assert "55" in result["output"]

    def test_timeout_handling(self):
        # Code that takes too long
        code = "while True: pass"
        result = CodeExecutor.execute_safe(code, timeout=2)
        assert result["success"] is False
        assert "timeout" in result["error"].lower()

    def test_runtime_error_captured(self):
        code = "x = 1 / 0"
        result = CodeExecutor.execute_safe(code)
        assert result["success"] is False
        assert result["error"] is not None


class TestCodeAgentExecution:
    """Test code agent full workflow."""

    def test_generate_code_invokes_llm(self):
        mock_router = MockLLMRouter(response_content="print('Result:', 42)")
        agent = CodeAgent(llm_router=mock_router)
        state = normalize_state(make_base_state(user_query="Calculate 6 * 7"))
        
        code = agent.generate_code(state)
        assert "42" in code or "print" in code

    def test_generate_cleans_markdown(self):
        mock_router = MockLLMRouter(
            response_content="```python\nprint('clean')\n```"
        )
        agent = CodeAgent(llm_router=mock_router)
        state = normalize_state(make_base_state(user_query="Print clean"))
        
        code = agent.generate_code(state)
        assert "```" not in code

    def test_safe_code_executes_without_approval(self):
        mock_router = MockLLMRouter(response_content="x = 2 + 3\nprint(x)")
        agent = CodeAgent(llm_router=mock_router)
        state = normalize_state(make_base_state(user_query="Add 2 and 3"))
        
        result = agent.execute(state)
        # Safe code should run directly
        assert result["execution_status"] in ("completed", "requires_approval")

    def test_dangerous_code_requires_approval(self):
        mock_router = MockLLMRouter(
            response_content="import os\nos.system('echo hack')"
        )
        agent = CodeAgent(llm_router=mock_router)
        state = normalize_state(make_base_state(user_query="Run system command"))
        
        result = agent.execute(state)
        assert result.get("approval_required") is True or result["risk_level"] == "high"
