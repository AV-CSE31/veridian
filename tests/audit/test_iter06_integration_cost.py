"""
ADVERSARIAL AUDIT — Iteration 6: Integration cost for a Monday adopter.

The WorkerAgent system prompt DOES ship the <veridian:result> contract to the
model (noted; not silent coupling). The integration tax is elsewhere:
misconfiguration fails late and cryptically, and is not isolated per task.

  I6-1 (P1): an unknown verifier_id is accepted by ledger.add(); the error only
             surfaces when the task runs. Queue 500 tasks, discover the typo on
             task 500.
  I6-2 (P1): one task's bad verifier config is not isolated — it can crash the
             run, taking healthy sibling tasks down with it.
  I6-3 (P2): a typo'd verifier_config key raises a raw Python TypeError from deep
             in the stack at run time, not a Veridian config error at setup.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from veridian import MockProvider, Task, TaskLedger, VeridianRunner
from veridian.core.config import VeridianConfig
from veridian.core.exceptions import VerifierNotFound


def _env(tmp_path: Path) -> tuple[VeridianConfig, TaskLedger]:
    cfg = VeridianConfig(
        ledger_file=tmp_path / "ledger.json",
        progress_file=str(tmp_path / "progress.md"),
    )
    return cfg, TaskLedger(cfg.ledger_file, progress_file=str(cfg.progress_file))


def test_I6_1_unknown_verifier_id_fails_fast_at_add(tmp_path: Path) -> None:
    """A typo'd verifier_id should be rejected when the task is added, not 499
    tasks later when it finally runs.
    """
    _cfg, led = _env(tmp_path)
    with pytest.raises(VerifierNotFound):
        led.add(
            [
                Task(
                    title="t",
                    verifier_id="shema",  # typo of "schema"
                    verifier_config={"required_fields": ["ok"]},
                )
            ]
        )
    # If we get here without raising, add() silently accepted a task that can
    # never verify.
    stored = led.list()
    assert not stored, (
        "ledger.add() accepted a task with verifier_id='shema' (a typo). No "
        "verifier-existence check at add time, so the failure is deferred to run "
        "time — a queued batch hides bad config until the bad task executes."
    )


def test_I6_2_config_error_is_distinguishable_from_verification_failure(tmp_path: Path) -> None:
    """CORRECTED CLAIM (the runner DOES isolate the bad task — it does not crash
    siblings; that part of my hypothesis was wrong and is retracted).

    The real defect: a permanent configuration error (typo'd config key ->
    TypeError at verifier construction) is caught and laundered into a generic
    verification 'failed' with the raw TypeError as last_error. An operator
    scanning FAILED tasks cannot tell 'the agent produced a wrong answer' from
    'you mistyped the verifier config' — and the impossible task burns the retry
    budget (and, with a real provider, real LLM calls) on a failure that can
    never succeed.
    """
    cfg, led = _env(tmp_path)
    led.add(
        [
            Task(
                title="broken", verifier_id="schema", verifier_config={"requried_fields": ["ok"]}
            )  # typo key -> TypeError
        ]
    )
    provider = MockProvider().script_veridian_result(structured={"ok": "yes"})
    VeridianRunner(ledger=led, provider=provider, config=cfg).run()

    broken = led.get([t for t in led.list()][0].id)
    last_error = (broken.last_error or "").lower()
    looks_like_config_error = any(
        kw in last_error for kw in ("config", "configuration", "verifier_config", "setup")
    )
    assert looks_like_config_error, (
        f"Config typo surfaced as a generic verification failure "
        f"(last_error={broken.last_error!r}). A permanent setup error is "
        "indistinguishable from a wrong-answer failure and is retried like a "
        "transient one — wasted budget plus a misleading triage signal."
    )


def test_I6_3_typo_config_key_raises_veridian_config_error(tmp_path: Path) -> None:
    """A typo'd config key should surface as a Veridian configuration error with
    guidance, not a raw TypeError from a verifier constructor.
    """
    from veridian.core.exceptions import VeridianConfigError, VeridianError
    from veridian.verify.base import registry

    try:
        registry.get("schema", {"requried_fields": ["x"]})
    except (VeridianConfigError, VeridianError):
        return  # acceptable: typed, actionable
    except TypeError as exc:
        pytest.fail(
            f"Typo'd config key surfaced as a raw TypeError ({exc}). The registry "
            "passes config straight into __init__ as **kwargs, so config mistakes "
            "look like framework bugs, not user errors."
        )
