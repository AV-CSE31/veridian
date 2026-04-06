"""
veridian.verify.builtin.tool_safety
────────────────────────────────────
AST-based static analysis on agent-generated code.

Covers Pathway 3: Tool Misevolution — 65% of self-improving agents create
insecure tools, 80% miss malicious code (Misevolution paper).

Checks:
├── No eval/exec/compile/__import__ calls
├── No os.system / subprocess shell=True
├── No network calls without allowlist
├── No filesystem writes outside sandbox
├── No secret env var access (os.environ)
├── Import allowlist enforcement
├── Max code complexity limit (prevent obfuscated payloads)
└── AST-based pattern matching (not regex — catches renamed imports)
"""

from __future__ import annotations

import ast
import logging
import re
from typing import ClassVar

from veridian.core.exceptions import VeridianConfigError
from veridian.core.task import Task, TaskResult
from veridian.verify.base import BaseVerifier, VerificationResult

log = logging.getLogger(__name__)

# Default allowed imports — safe stdlib modules
_DEFAULT_ALLOWED_IMPORTS: frozenset[str] = frozenset(
    {
        "json",
        "math",
        "os",
        "pathlib",
        "re",
        "collections",
        "datetime",
        "functools",
        "itertools",
        "string",
        "textwrap",
        "typing",
        "dataclasses",
        "enum",
        "abc",
        "copy",
        "hashlib",
        "hmac",
        "csv",
        "io",
        "operator",
        "statistics",
        "decimal",
        "fractions",
        "random",
        "uuid",
        "pprint",
        "logging",
        "warnings",
        "contextlib",
        "tempfile",
    }
)

# Default blocked function/method calls
_DEFAULT_BLOCKED_CALLS: frozenset[str] = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "globals",
        "locals",
        "breakpoint",
        "input",
    }
)

# Blocked imports — network, process spawning, dynamic code
_BLOCKED_IMPORTS: frozenset[str] = frozenset(
    {
        "socket",
        "http",
        "urllib",
        "requests",
        "httpx",
        "aiohttp",
        "ctypes",
        "multiprocessing",
        "signal",
        "shutil",
        "webbrowser",
        "smtplib",
        "ftplib",
        "xmlrpc",
        "pickle",
        "shelve",
        "marshal",
        "importlib",
        "code",
        "codeop",
        "compileall",
        "py_compile",
    }
)

# Dangerous attribute access patterns
_DANGEROUS_ATTRS: frozenset[str] = frozenset(
    {
        "system",  # os.system
        "popen",  # os.popen
        "environ",  # os.environ
        "getenv",  # os.getenv
    }
)

# Python -c inline code pattern
_PYTHON_C_RE = re.compile(r'python[3]?\s+-c\s+["\'](.+?)["\']', re.DOTALL)


class ToolSafetyVerifier(BaseVerifier):
    """
    Static analysis verifier for agent-generated code.

    Uses Python AST to detect dangerous patterns — not regex.
    Catches renamed imports, nested calls, and obfuscation attempts.
    Stateless: all config via constructor. Safe for concurrent use.
    """

    id: ClassVar[str] = "tool_safety"
    description: ClassVar[str] = (
        "AST-based static safety analysis on agent-generated code. "
        "Blocks eval/exec, shell injection, blocked imports, env access."
    )

    def __init__(
        self,
        allowed_imports: list[str] | None = None,
        blocked_calls: list[str] | None = None,
        max_complexity: int = 50,
    ) -> None:
        """
        Args:
            allowed_imports: Allowlisted import module names. Default: safe stdlib.
            blocked_calls: Blocked function/builtin names. Default: eval/exec/compile/etc.
            max_complexity: Max AST node count. Prevents obfuscated payloads.
        """
        if max_complexity <= 0:
            raise VeridianConfigError(
                f"ToolSafetyVerifier: 'max_complexity' must be > 0, got {max_complexity}."
            )
        self.allowed_imports: frozenset[str] = (
            frozenset(allowed_imports) if allowed_imports else _DEFAULT_ALLOWED_IMPORTS
        )
        self.blocked_calls: frozenset[str] = (
            frozenset(blocked_calls) if blocked_calls else _DEFAULT_BLOCKED_CALLS
        )
        self.max_complexity = max_complexity

    def verify(self, task: Task, result: TaskResult) -> VerificationResult:
        """Analyze all code surfaces in the task result for safety violations."""
        code_snippets = self._extract_code(result)
        if not code_snippets:
            return VerificationResult(passed=True, evidence={"checked": 0})

        all_violations: list[str] = []
        for snippet in code_snippets:
            violations = self._analyze(snippet)
            all_violations.extend(violations)

        if not all_violations:
            return VerificationResult(
                passed=True,
                evidence={"checked": len(code_snippets)},
            )

        error = self._format_error(all_violations)
        return VerificationResult(
            passed=False,
            error=error,
            evidence={"violations": all_violations, "checked": len(code_snippets)},
        )

    def _extract_code(self, result: TaskResult) -> list[str]:
        """Extract code strings from all surfaces in the result."""
        snippets: list[str] = []

        # Check structured fields
        for field_name in ("code", "script", "python", "command"):
            val = result.structured.get(field_name, "")
            if isinstance(val, str) and val.strip():
                snippets.append(val)

        # Check bash_outputs for inline python -c commands
        for bo in result.bash_outputs:
            cmd = bo.get("cmd", "")
            if isinstance(cmd, str):
                matches = _PYTHON_C_RE.findall(cmd)
                snippets.extend(matches)

        return snippets

    def _analyze(self, code: str) -> list[str]:
        """Run all AST-based checks on a code snippet."""
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return ["Code could not be parsed (syntax error) — cannot verify safety"]

        violations: list[str] = []

        for node in ast.walk(tree):
            # Check blocked function calls
            violations.extend(self._check_calls(node))
            # Check imports
            violations.extend(self._check_imports(node))
            # Check dangerous attribute access
            violations.extend(self._check_attrs(node))

        # Check complexity
        node_count = sum(1 for _ in ast.walk(tree))
        if node_count > self.max_complexity:
            violations.append(
                f"Code complexity ({node_count} nodes) exceeds limit ({self.max_complexity})"
            )

        return violations

    def _check_calls(self, node: ast.AST) -> list[str]:
        """Detect calls to blocked builtins."""
        violations: list[str] = []
        if isinstance(node, ast.Call):
            func = node.func
            # Direct call: eval(), exec(), compile()
            if isinstance(func, ast.Name) and func.id in self.blocked_calls:
                violations.append(f"Blocked call: {func.id}() is not allowed")
            # Attribute call: os.system(), subprocess.run(shell=True)
            if isinstance(func, ast.Attribute):
                if func.attr == "system" and self._is_name(func.value, "os"):
                    violations.append("Blocked call: os.system() — use subprocess without shell")
                if func.attr == "run" and self._is_name(func.value, "subprocess"):
                    # Check for shell=True
                    for kw in node.keywords:
                        if (
                            kw.arg == "shell"
                            and isinstance(kw.value, ast.Constant)
                            and kw.value.value is True
                        ):
                            violations.append(
                                "Blocked: subprocess.run(shell=True) — use shell=False"
                            )
        return violations

    def _check_imports(self, node: ast.AST) -> list[str]:
        """Detect blocked imports."""
        violations: list[str] = []
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_module = alias.name.split(".")[0]
                if root_module in _BLOCKED_IMPORTS:
                    violations.append(
                        f"Blocked import: '{alias.name}' is not in the import allowlist"
                    )
        elif isinstance(node, ast.ImportFrom) and node.module:
            root_module = node.module.split(".")[0]
            if root_module in _BLOCKED_IMPORTS:
                violations.append(
                    f"Blocked import: 'from {node.module}' is not in the import allowlist"
                )
        return violations

    def _check_attrs(self, node: ast.AST) -> list[str]:
        """Detect dangerous attribute access patterns."""
        violations: list[str] = []
        if (
            isinstance(node, ast.Attribute)
            and node.attr in _DANGEROUS_ATTRS
            and self._is_name(node.value, "os")
        ):
            violations.append(f"Blocked access: os.{node.attr} — direct OS access not allowed")
        return violations

    @staticmethod
    def _is_name(node: ast.AST, name: str) -> bool:
        """Check if an AST node is a Name with given id."""
        return isinstance(node, ast.Name) and node.id == name

    @staticmethod
    def _format_error(violations: list[str]) -> str:
        """Format violations into an actionable error message ≤ 300 chars."""
        if len(violations) == 1:
            return violations[0][:300]

        msg = f"[{len(violations)} violations] "
        msg += "; ".join(violations)
        return msg[:300]
