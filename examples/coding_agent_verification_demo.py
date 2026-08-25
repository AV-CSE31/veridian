"""Killer demo: block false "done" claims from a coding agent.

The demo creates a tiny real git repo, simulates two agent runs, and gates each
claim with a Veridian completion contract:

1. Positive path: tests, coverage, py_compile, and repo diff guard pass.
2. Negative path: tests still pass, but the agent also writes a secret to
   `.env`; Veridian blocks the merge even though the agent claims success.

Run:
    python examples/coding_agent_verification_demo.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from veridian import VerificationContract, VerifierStep, verify_completion

# Demonstration material only. Production callers must load a unique secret
# from their secret manager and retain the resulting chain head independently.
DEMO_PROOF_SIGNING_KEY = "demo-only-veridian-proof-key-2026-0001"


def main() -> None:
    workspace = Path("demo_runs") / "coding_agent_verification"
    if workspace.exists():
        _remove_tree(workspace)
    workspace.mkdir(parents=True)
    good_repo = _create_buggy_repo(workspace / "good-repo")
    bad_repo = _create_buggy_repo(workspace / "bad-repo")

    good_decision = _run_agent_claim(good_repo, inject_secret=False)
    bad_decision = _run_agent_claim(bad_repo, inject_secret=True)

    print("Positive path:")
    print(f"  repo: {good_repo}")
    print(f"  passed: {good_decision.passed}")
    print(f"  proof: {good_repo / 'veridian-proof.jsonl'}")
    print(f"  PR comment: {good_repo / 'veridian-pr-comment.md'}")
    print()
    print("Negative path:")
    print(f"  repo: {bad_repo}")
    print(f"  passed: {bad_decision.passed}")
    print(f"  feedback: {bad_decision.feedback[0] if bad_decision.feedback else 'n/a'}")
    print(f"  proof: {bad_repo / 'veridian-proof.jsonl'}")
    print(f"  PR comment: {bad_repo / 'veridian-pr-comment.md'}")


def _run_agent_claim(repo: Path, *, inject_secret: bool):
    _scripted_agent_fix(repo, inject_secret=inject_secret)
    contract = VerificationContract(
        contract_id="coding_agent_release_gate",
        description=(
            "Accept a coding-agent bug fix only when tests/coverage pass, "
            "syntax compiles, changed files are allowed, protected paths are "
            "untouched, and no obvious secrets are introduced."
        ),
        verifiers=[
            VerifierStep(
                name="tests-and-coverage",
                verifier_id="bash_exit",
                verifier_config={
                    "command": (
                        f"{sys.executable} -m pytest -q --cov=tiny_calc "
                        "--cov-report=term-missing --cov-fail-under=90"
                    ),
                    "cwd": str(repo),
                    "timeout_seconds": 60,
                    "inherit_env": True,
                },
            ),
            VerifierStep(
                name="syntax-check",
                verifier_id="bash_exit",
                verifier_config={
                    "command": f"{sys.executable} -m py_compile tiny_calc.py",
                    "cwd": str(repo),
                    "timeout_seconds": 20,
                    "inherit_env": True,
                },
            ),
            VerifierStep(
                name="repo-diff-guard",
                verifier_id="repo_guard",
                verifier_config={
                    "repo_root": str(repo),
                    "allowed_paths": ["tiny_calc.py"],
                    "protected_paths": [".env", ".env.*", ".github/*", "secrets/*"],
                    "require_changes": True,
                },
            ),
        ],
        evidence_files=[str(repo / "tiny_calc.py"), str(repo / "tests" / "test_tiny_calc.py")],
        metadata={"demo": "coding-agent-verification"},
    )
    decision = verify_completion(
        contract=contract,
        input_payload={"bug": "add() subtracts instead of adding"},
        output_payload={
            "agent_claim": "Bug fixed and tests pass.",
            "changed_files": _git_changed_files(repo),
        },
        proof_file=repo / "veridian-proof.jsonl",
        signing_key=DEMO_PROOF_SIGNING_KEY,
    )
    (repo / "veridian-pr-comment.md").write_text(decision.to_pr_comment(), encoding="utf-8")
    return decision


def _create_buggy_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "tiny_calc.py").write_text(
        "def add(a: int, b: int) -> int:\n"
        '    """Return the sum of two integers."""\n'
        "    return a - b\n",
        encoding="utf-8",
    )
    tests = path / "tests"
    tests.mkdir()
    (tests / "test_tiny_calc.py").write_text(
        "from tiny_calc import add\n\n\n"
        "def test_adds_positive_numbers():\n"
        "    assert add(2, 3) == 5\n\n\n"
        "def test_adds_negative_numbers():\n"
        "    assert add(-2, -3) == -5\n",
        encoding="utf-8",
    )
    _run(["git", "init"], cwd=path)
    _run(["git", "config", "user.email", "demo@example.com"], cwd=path)
    _run(["git", "config", "user.name", "Veridian Demo"], cwd=path)
    _run(["git", "add", "."], cwd=path)
    _run(["git", "commit", "-m", "buggy baseline"], cwd=path)
    return path


def _scripted_agent_fix(repo: Path, *, inject_secret: bool) -> None:
    (repo / "tiny_calc.py").write_text(
        "def add(a: int, b: int) -> int:\n"
        '    """Return the sum of two integers."""\n'
        "    return a + b\n",
        encoding="utf-8",
    )
    if inject_secret:
        (repo / ".env").write_text(
            "OPENAI_API_KEY=sk-demo-secret-that-should-never-ship\n",
            encoding="utf-8",
        )


def _git_changed_files(repo: Path) -> list[str]:
    proc = _run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=repo,
    )
    changed: list[str] = []
    for line in proc.stdout.splitlines():
        if len(line) >= 4:
            changed.append(line[3:].strip().replace("\\", "/"))
    return changed


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    executable = shutil.which(command[0])
    if executable is not None:
        command = [executable, *command[1:]]
    return subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True)


def _remove_tree(path: Path) -> None:
    def _onerror(function, failed_path, _exc_info):  # type: ignore[no-untyped-def]
        os.chmod(failed_path, 0o700)
        function(failed_path)

    shutil.rmtree(path, onerror=_onerror)


if __name__ == "__main__":
    main()
