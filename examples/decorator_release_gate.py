"""Verify an ordinary Python function with a Veridian decorator.

Run with:

    python examples/decorator_release_gate.py

The example is deterministic and makes zero network calls.
"""

from __future__ import annotations

from veridian import verified

RELEASE_CONTRACT = {
    "required": ["decision", "risk", "reason"],
    "properties": {
        "decision": {"type": "string", "enum": ["ship", "hold"]},
        "risk": {"type": "string", "enum": ["low", "medium", "high"]},
        "reason": {"type": "string", "minLength": 8},
    },
}


@verified(
    verifier_id="schema",
    verifier_config={"schema": RELEASE_CONTRACT},
    title="Release gate",
)
def decide_release(build_id: str, *, include_reason: bool) -> dict[str, str]:
    """Return a release decision that must satisfy RELEASE_CONTRACT."""
    decision = {"decision": "ship", "risk": "low"}
    if include_reason:
        decision["reason"] = f"{build_id} passed tests, lint, and verification"
    return decision


def main() -> None:
    good = decide_release("2026.05", include_reason=True)
    bad = decide_release("2026.05", include_reason=False)

    print(f"good passed={good.passed} payload={good.structured}")
    print(f"bad passed={bad.passed} error={bad.error!r}")


if __name__ == "__main__":
    main()
