"""
tests.unit.test_tool_safety
────────────────────────────
ToolSafetyVerifier — AST-based static analysis on agent-generated code.

Covers Pathway 3: Tool Misevolution (65% of self-improving agents create
insecure tools, 80% miss malicious code — Misevolution paper).
"""

from __future__ import annotations

import pytest

from veridian.core.exceptions import VeridianConfigError
from veridian.core.task import Task, TaskResult
from veridian.verify.builtin.tool_safety import ToolSafetyVerifier


class TestToolSafetyVerifierInit:
    """Config validation tests."""

    def test_default_config(self) -> None:
        """Should construct with sensible defaults."""
        v = ToolSafetyVerifier()
        assert v.id == "tool_safety"
        assert isinstance(v.allowed_imports, frozenset)
        assert isinstance(v.blocked_calls, frozenset)
        assert v.max_complexity > 0

    def test_custom_allowed_imports(self) -> None:
        """Should accept custom import allowlist."""
        v = ToolSafetyVerifier(allowed_imports=["os", "json", "math"])
        assert "os" in v.allowed_imports
        assert "json" in v.allowed_imports

    def test_custom_blocked_calls(self) -> None:
        """Should accept custom blocked call list."""
        v = ToolSafetyVerifier(blocked_calls=["eval", "exec"])
        assert "eval" in v.blocked_calls

    def test_max_complexity_must_be_positive(self) -> None:
        """Should reject non-positive max_complexity."""
        with pytest.raises(VeridianConfigError, match="max_complexity"):
            ToolSafetyVerifier(max_complexity=0)

    def test_max_complexity_negative(self) -> None:
        """Should reject negative max_complexity."""
        with pytest.raises(VeridianConfigError, match="max_complexity"):
            ToolSafetyVerifier(max_complexity=-5)


class TestToolSafetyPassCases:
    """Verifier should PASS safe code."""

    @pytest.fixture
    def verifier(self) -> ToolSafetyVerifier:
        return ToolSafetyVerifier()

    @pytest.fixture
    def task(self) -> Task:
        return Task(id="t1", title="Test task", verifier_id="tool_safety")

    def test_passes_safe_code(self, verifier: ToolSafetyVerifier, task: Task) -> None:
        """Should pass clean Python with allowed imports."""
        code = "import json\ndata = json.loads('{\"a\": 1}')\nprint(data)"
        result = TaskResult(raw_output="done", structured={"code": code})
        vr = verifier.verify(task, result)
        assert vr.passed is True

    def test_passes_no_code(self, verifier: ToolSafetyVerifier, task: Task) -> None:
        """Should pass when no code is present in structured output."""
        result = TaskResult(raw_output="done", structured={"summary": "no code here"})
        vr = verifier.verify(task, result)
        assert vr.passed is True

    def test_passes_empty_code(self, verifier: ToolSafetyVerifier, task: Task) -> None:
        """Should pass on empty code string."""
        result = TaskResult(raw_output="done", structured={"code": ""})
        vr = verifier.verify(task, result)
        assert vr.passed is True

    def test_passes_stdlib_imports(self, verifier: ToolSafetyVerifier, task: Task) -> None:
        """Should pass code importing from allowed stdlib modules."""
        code = "import os\nimport pathlib\nfrom collections import defaultdict\nprint('ok')"
        result = TaskResult(raw_output="done", structured={"code": code})
        vr = verifier.verify(task, result)
        assert vr.passed is True

    def test_passes_math_operations(self, verifier: ToolSafetyVerifier, task: Task) -> None:
        """Should pass simple math/data processing code."""
        code = "x = [1, 2, 3]\nresult = sum(x) / len(x)\nprint(result)"
        result = TaskResult(raw_output="done", structured={"code": code})
        vr = verifier.verify(task, result)
        assert vr.passed is True


class TestToolSafetyFailCases:
    """Verifier should FAIL dangerous code patterns."""

    @pytest.fixture
    def verifier(self) -> ToolSafetyVerifier:
        return ToolSafetyVerifier()

    @pytest.fixture
    def task(self) -> Task:
        return Task(id="t1", title="Test task", verifier_id="tool_safety")

    def test_fails_eval(self, verifier: ToolSafetyVerifier, task: Task) -> None:
        """Should fail code containing eval()."""
        code = "result = eval('2 + 2')"
        result = TaskResult(raw_output="done", structured={"code": code})
        vr = verifier.verify(task, result)
        assert vr.passed is False
        assert "eval" in vr.error.lower()

    def test_fails_exec(self, verifier: ToolSafetyVerifier, task: Task) -> None:
        """Should fail code containing exec()."""
        code = "exec('import os; os.system(\"rm -rf /\")')"
        result = TaskResult(raw_output="done", structured={"code": code})
        vr = verifier.verify(task, result)
        assert vr.passed is False
        assert "exec" in vr.error.lower()

    def test_fails_os_system(self, verifier: ToolSafetyVerifier, task: Task) -> None:
        """Should fail code using os.system()."""
        code = "import os\nos.system('rm -rf /')"
        result = TaskResult(raw_output="done", structured={"code": code})
        vr = verifier.verify(task, result)
        assert vr.passed is False

    def test_fails_subprocess_shell(self, verifier: ToolSafetyVerifier, task: Task) -> None:
        """Should fail code using subprocess with shell=True."""
        code = "import subprocess\nsubprocess.run('ls', shell=True)"
        result = TaskResult(raw_output="done", structured={"code": code})
        vr = verifier.verify(task, result)
        assert vr.passed is False
        assert "subprocess" in vr.error.lower() or "shell" in vr.error.lower()

    def test_fails_blocked_import(self, verifier: ToolSafetyVerifier, task: Task) -> None:
        """Should fail code importing blocked modules."""
        code = "import socket\ns = socket.socket()"
        result = TaskResult(raw_output="done", structured={"code": code})
        vr = verifier.verify(task, result)
        assert vr.passed is False
        assert "socket" in vr.error.lower()

    def test_fails_compile_builtin(self, verifier: ToolSafetyVerifier, task: Task) -> None:
        """Should fail code using compile()."""
        code = "code_obj = compile('print(1)', '<string>', 'exec')"
        result = TaskResult(raw_output="done", structured={"code": code})
        vr = verifier.verify(task, result)
        assert vr.passed is False

    def test_fails_getattr_on_sensitive(self, verifier: ToolSafetyVerifier, task: Task) -> None:
        """Should fail code using __import__."""
        code = "__import__('os').system('whoami')"
        result = TaskResult(raw_output="done", structured={"code": code})
        vr = verifier.verify(task, result)
        assert vr.passed is False

    def test_fails_env_var_access(self, verifier: ToolSafetyVerifier, task: Task) -> None:
        """Should fail code accessing os.environ for secrets."""
        code = "import os\npassword = os.environ['DB_PASSWORD']"
        result = TaskResult(raw_output="done", structured={"code": code})
        vr = verifier.verify(task, result)
        assert vr.passed is False

    def test_fails_multiple_violations(self, verifier: ToolSafetyVerifier, task: Task) -> None:
        """Should report multiple violations."""
        code = "import socket\neval('1+1')\nexec('print(1)')"
        result = TaskResult(raw_output="done", structured={"code": code})
        vr = verifier.verify(task, result)
        assert vr.passed is False
        # Evidence should contain the violation list
        assert "violations" in vr.evidence
        assert len(vr.evidence["violations"]) >= 2


class TestToolSafetyErrorMessages:
    """Error messages must be actionable and within budget."""

    @pytest.fixture
    def verifier(self) -> ToolSafetyVerifier:
        return ToolSafetyVerifier()

    @pytest.fixture
    def task(self) -> Task:
        return Task(id="t1", title="Test task", verifier_id="tool_safety")

    def test_error_message_within_budget(
        self, verifier: ToolSafetyVerifier, task: Task
    ) -> None:
        """Error messages must be ≤ 300 chars."""
        code = "eval('1')\nexec('2')\nimport socket\nimport ctypes\ncompile('x', '', 'exec')"
        result = TaskResult(raw_output="done", structured={"code": code})
        vr = verifier.verify(task, result)
        assert vr.passed is False
        assert len(vr.error) <= 300

    def test_error_names_violation(
        self, verifier: ToolSafetyVerifier, task: Task
    ) -> None:
        """Error should name what was blocked."""
        code = "eval('1+1')"
        result = TaskResult(raw_output="done", structured={"code": code})
        vr = verifier.verify(task, result)
        assert "eval" in vr.error.lower()


class TestToolSafetyCodeField:
    """Verifier should find code in multiple locations."""

    @pytest.fixture
    def verifier(self) -> ToolSafetyVerifier:
        return ToolSafetyVerifier()

    @pytest.fixture
    def task(self) -> Task:
        return Task(id="t1", title="Test task", verifier_id="tool_safety")

    def test_checks_code_field(self, verifier: ToolSafetyVerifier, task: Task) -> None:
        """Should check structured['code'] field."""
        result = TaskResult(raw_output="safe", structured={"code": "eval('x')"})
        vr = verifier.verify(task, result)
        assert vr.passed is False

    def test_checks_script_field(self, verifier: ToolSafetyVerifier, task: Task) -> None:
        """Should check structured['script'] field."""
        result = TaskResult(raw_output="safe", structured={"script": "eval('x')"})
        vr = verifier.verify(task, result)
        assert vr.passed is False

    def test_checks_bash_outputs(self, verifier: ToolSafetyVerifier, task: Task) -> None:
        """Should scan bash_outputs for inline Python."""
        result = TaskResult(
            raw_output="done",
            structured={},
            bash_outputs=[{"cmd": "python -c \"eval('1+1')\"", "exit_code": 0}],
        )
        vr = verifier.verify(task, result)
        assert vr.passed is False

    def test_unparseable_code_fails(self, verifier: ToolSafetyVerifier, task: Task) -> None:
        """Should fail gracefully on syntax errors (can't verify safety = unsafe)."""
        code = "def broken(\n"  # syntax error
        result = TaskResult(raw_output="done", structured={"code": code})
        vr = verifier.verify(task, result)
        assert vr.passed is False
        assert "parse" in vr.error.lower() or "syntax" in vr.error.lower()


class TestToolSafetyStateless:
    """Verifier must be stateless — safe for concurrent use."""

    def test_multiple_calls_independent(self) -> None:
        """Sequential calls should not affect each other."""
        v = ToolSafetyVerifier()
        task = Task(id="t1", title="Test", verifier_id="tool_safety")

        safe = TaskResult(raw_output="ok", structured={"code": "x = 1"})
        unsafe = TaskResult(raw_output="ok", structured={"code": "eval('1')"})

        assert v.verify(task, safe).passed is True
        assert v.verify(task, unsafe).passed is False
        assert v.verify(task, safe).passed is True  # not poisoned by prior call
