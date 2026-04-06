"""
veridian.cli.replay
────────────────────
RV3-006: Replay/diff CLI for operator debugging.

Three commands:
- ``veridian replay show <task_id>``  — dump journal/snapshot/pause state for a task
- ``veridian replay diff <a> <b>``    — diff two runs of the same task
- ``veridian replay compare <l1> <l2>`` — diff a task across two ledger files

Operators answer "what changed?" without reading raw JSON.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from veridian.ledger.ledger import TaskLedger

replay_app = typer.Typer(
    name="replay",
    help="Inspect and diff replay artifacts (activity journal, snapshot, pause state).",
    no_args_is_help=True,
)
console = Console()


def _load_ledger(path: str) -> TaskLedger:
    p = Path(path)
    if not p.exists():
        console.print(f"[red]Error:[/red] Ledger not found at {p}")
        raise typer.Exit(code=1)
    return TaskLedger(path=p)


def _extract_replay_artifacts(result: Any) -> dict[str, Any]:
    """Pull replay-relevant fields out of a TaskResult for pretty-printing."""
    if result is None:
        return {}
    extras = getattr(result, "extras", {}) or {}
    return {
        "run_replay_snapshot": extras.get("run_replay_snapshot"),
        "activity_journal": extras.get("activity_journal", []),
        "pause_payload": extras.get("pause_payload"),
        "policy_action_log": extras.get("policy_action_log", []),
        "prm_checkpoint": extras.get("prm_checkpoint"),
        "verifier_score": getattr(result, "verifier_score", None),
        "verification_evidence": getattr(result, "verification_evidence", {}),
        "verified": getattr(result, "verified", False),
    }


@replay_app.command("show")
def show(
    task_id: str = typer.Argument(..., help="Task ID to inspect"),
    ledger: str = typer.Option("ledger.json", "--ledger", "-l"),
    json_output: bool = typer.Option(False, "--json", help="Emit raw JSON"),
) -> None:
    """Show replay artifacts for a single task."""
    task_ledger = _load_ledger(ledger)
    try:
        task = task_ledger.get(task_id)
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    artifacts = _extract_replay_artifacts(task.result)
    payload = {
        "task_id": task.id,
        "title": task.title,
        "status": task.status.value,
        "retry_count": task.retry_count,
        "last_error": task.last_error,
        **artifacts,
    }

    if json_output:
        console.print_json(data=payload)
        return

    console.print(f"[bold]Task {task.id}[/bold] — {task.title}")
    console.print(f"  status: [cyan]{task.status.value}[/cyan]")
    console.print(f"  retry_count: {task.retry_count}")
    snap = artifacts.get("run_replay_snapshot") or {}
    if snap:
        console.print("  [bold]replay snapshot[/bold]:")
        for k, v in snap.items():
            console.print(f"    {k}: {v}")
    journal = artifacts.get("activity_journal") or []
    if journal:
        console.print(f"  [bold]activity journal[/bold]: {len(journal)} entries")
        table = Table(show_header=True)
        table.add_column("idempotency_key")
        table.add_column("fn_name")
        table.add_column("status")
        table.add_column("attempts")
        for entry in journal[:20]:
            table.add_row(
                str(entry.get("idempotency_key", ""))[:40],
                str(entry.get("fn_name", "")),
                str(entry.get("status", "")),
                str(entry.get("attempts", "")),
            )
        console.print(table)
    pause = artifacts.get("pause_payload")
    if pause:
        console.print(f"  [yellow]pause_payload[/yellow]: {pause}")
    policy_log = artifacts.get("policy_action_log") or []
    if policy_log:
        console.print(f"  policy_action_log: {len(policy_log)} entries")


def _diff_dicts(a: dict[str, Any], b: dict[str, Any]) -> list[tuple[str, Any, Any]]:
    """Return list of (key, value_a, value_b) for keys that differ."""
    diffs: list[tuple[str, Any, Any]] = []
    keys = sorted(set(a.keys()) | set(b.keys()))
    for k in keys:
        va = a.get(k)
        vb = b.get(k)
        if va != vb:
            diffs.append((k, va, vb))
    return diffs


@replay_app.command("compare")
def compare(
    task_id: str = typer.Argument(..., help="Task ID to diff"),
    ledger_a: str = typer.Argument(..., help="First ledger file (baseline)"),
    ledger_b: str = typer.Argument(..., help="Second ledger file (candidate)"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Diff the same task across two ledger files (A=baseline, B=candidate)."""
    la = _load_ledger(ledger_a)
    lb = _load_ledger(ledger_b)
    try:
        ta = la.get(task_id)
        tb = lb.get(task_id)
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    artifacts_a = _extract_replay_artifacts(ta.result)
    artifacts_b = _extract_replay_artifacts(tb.result)

    # Compare snapshots field-by-field
    snap_a = artifacts_a.get("run_replay_snapshot") or {}
    snap_b = artifacts_b.get("run_replay_snapshot") or {}
    snap_diffs = (
        _diff_dicts(snap_a, snap_b) if isinstance(snap_a, dict) and isinstance(snap_b, dict) else []
    )

    # Compare top-level task fields
    task_diffs = _diff_dicts(
        {"status": ta.status.value, "retry_count": ta.retry_count, "last_error": ta.last_error},
        {"status": tb.status.value, "retry_count": tb.retry_count, "last_error": tb.last_error},
    )

    journal_a = artifacts_a.get("activity_journal") or []
    journal_b = artifacts_b.get("activity_journal") or []

    payload = {
        "task_id": task_id,
        "task_diffs": [{"field": k, "a": va, "b": vb} for k, va, vb in task_diffs],
        "snapshot_diffs": [{"field": k, "a": va, "b": vb} for k, va, vb in snap_diffs],
        "activity_journal": {
            "a_count": len(journal_a),
            "b_count": len(journal_b),
            "changed": journal_a != journal_b,
        },
    }

    if json_output:
        console.print_json(data=payload)
        return

    console.print(f"[bold]Diff task {task_id}[/bold]")
    if task_diffs:
        console.print("[yellow]task field diffs:[/yellow]")
        for k, va, vb in task_diffs:
            console.print(f"  {k}: {va!r} → {vb!r}")
    if snap_diffs:
        console.print("[yellow]snapshot diffs:[/yellow]")
        for k, va, vb in snap_diffs:
            console.print(f"  {k}: {va} → {vb}")
    if not task_diffs and not snap_diffs:
        console.print("[green]No differences in task fields or replay snapshot.[/green]")
    console.print(
        f"activity journal: a={len(journal_a)} b={len(journal_b)} changed={journal_a != journal_b}"
    )


@replay_app.command("diff")
def diff(
    task_id_a: str = typer.Argument(..., help="First task ID"),
    task_id_b: str = typer.Argument(..., help="Second task ID"),
    ledger: str = typer.Option("ledger.json", "--ledger", "-l"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Diff two tasks from the same ledger (useful for A/B comparisons)."""
    task_ledger = _load_ledger(ledger)
    try:
        ta = task_ledger.get(task_id_a)
        tb = task_ledger.get(task_id_b)
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    a_artifacts = _extract_replay_artifacts(ta.result)
    b_artifacts = _extract_replay_artifacts(tb.result)
    snap_a = a_artifacts.get("run_replay_snapshot") or {}
    snap_b = b_artifacts.get("run_replay_snapshot") or {}
    diffs = (
        _diff_dicts(snap_a, snap_b) if isinstance(snap_a, dict) and isinstance(snap_b, dict) else []
    )

    payload = {
        "task_a": task_id_a,
        "task_b": task_id_b,
        "snapshot_diffs": [{"field": k, "a": va, "b": vb} for k, va, vb in diffs],
    }
    if json_output:
        console.print_json(data=payload)
        return

    console.print(f"[bold]Diff {task_id_a} ↔ {task_id_b}[/bold]")
    if not diffs:
        console.print("[green]Snapshots are identical.[/green]")
    else:
        for k, va, vb in diffs:
            console.print(f"  {k}: {va} → {vb}")
