"""
veridian.cli.dlq
─────────────────
Phase C: DLQ CLI commands — status, retry, dismiss, report.

Operators use these to inspect abandoned tasks, retry transient failures,
dismiss handled entries, and generate summary reports from the dead letter
queue.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from veridian.core.dlq import DeadLetterQueue, TriageCategory
from veridian.ledger.ledger import TaskLedger

dlq_app = typer.Typer(
    name="dlq",
    help="Inspect and manage the Dead Letter Queue (abandoned tasks).",
    no_args_is_help=True,
)
console = Console()

DLQ_OPT = typer.Option("dlq.json", "--dlq", help="Path to dlq.json")
LEDGER_OPT = typer.Option("ledger.json", "--ledger", "-l", help="Path to ledger.json")


def _load_dlq(path: str) -> DeadLetterQueue:
    p = Path(path)
    return DeadLetterQueue(storage_path=p, max_retries=100)


@dlq_app.command("status")
def status(
    dlq_path: str = DLQ_OPT,
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Show DLQ summary: counts by triage category."""
    dlq = _load_dlq(dlq_path)
    summary = dlq.summary()
    if json_output:
        console.print_json(data=summary)
        return
    console.print(f"[bold]DLQ status[/bold] ({dlq_path})")
    console.print(f"  total:     {summary['total']}")
    console.print(f"  transient: {summary['transient']}")
    console.print(f"  permanent: {summary['permanent']}")
    console.print(f"  unknown:   {summary['unknown']}")


@dlq_app.command("list")
def list_entries(
    dlq_path: str = DLQ_OPT,
    category: str | None = typer.Option(
        None, "--category", "-c", help="Filter: transient|permanent|unknown"
    ),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """List all DLQ entries, optionally filtered by triage category."""
    dlq = _load_dlq(dlq_path)
    cat = TriageCategory(category) if category else None
    entries = dlq.list_entries(category=cat)
    if json_output:
        console.print_json(data=[e.to_dict() for e in entries])
        return
    if not entries:
        console.print("[green]DLQ is empty.[/green]")
        return
    table = Table(show_header=True)
    table.add_column("task_id")
    table.add_column("category")
    table.add_column("retries")
    table.add_column("reason", max_width=60)
    for e in entries:
        table.add_row(
            e.task_id[:12],
            str(e.triage_category),
            str(e.retry_count),
            e.failure_reason[:60],
        )
    console.print(table)


@dlq_app.command("retry")
def retry(
    task_id: str = typer.Argument(..., help="Task ID to retry"),
    dlq_path: str = DLQ_OPT,
    ledger_path: str = LEDGER_OPT,
) -> None:
    """Re-queue a DLQ entry back to PENDING in the ledger and dismiss it."""
    dlq = _load_dlq(dlq_path)
    entry = dlq.get(task_id)
    if entry is None:
        console.print(f"[red]Error:[/red] Task {task_id!r} not found in DLQ")
        raise typer.Exit(code=1)
    if not dlq.is_retryable(entry):
        console.print(
            f"[yellow]Warning:[/yellow] Task {task_id!r} is {entry.triage_category} "
            f"and has exhausted {entry.retry_count} retries. Re-queuing anyway."
        )
    ledger = TaskLedger(path=Path(ledger_path))
    try:
        ledger.reset_failed([task_id])
    except Exception as exc:
        console.print(f"[red]Error:[/red] Could not re-queue in ledger: {exc}")
        raise typer.Exit(code=1) from exc
    dlq.dismiss(task_id)
    console.print(f"[green]Task {task_id!r} re-queued to PENDING and dismissed from DLQ.[/green]")


@dlq_app.command("dismiss")
def dismiss(
    task_id: str = typer.Argument(..., help="Task ID to dismiss"),
    dlq_path: str = DLQ_OPT,
) -> None:
    """Dismiss a DLQ entry (operator confirmed it is handled)."""
    dlq = _load_dlq(dlq_path)
    if dlq.get(task_id) is None:
        console.print(f"[red]Error:[/red] Task {task_id!r} not found in DLQ")
        raise typer.Exit(code=1)
    dlq.dismiss(task_id)
    console.print(f"[green]Task {task_id!r} dismissed from DLQ.[/green]")


@dlq_app.command("report")
def report(
    dlq_path: str = DLQ_OPT,
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Generate a structured triage report of all DLQ entries."""
    dlq = _load_dlq(dlq_path)
    entries = dlq.list_entries()
    report_data: dict[str, Any] = {
        "summary": dlq.summary(),
        "entries": [e.to_dict() for e in entries],
        "retryable": [e.task_id for e in entries if dlq.is_retryable(e)],
    }
    if json_output:
        console.print_json(data=report_data)
        return
    console.print("[bold]DLQ Triage Report[/bold]")
    summary = report_data["summary"]
    console.print(f"  Total: {summary['total']}")
    console.print(
        f"  Transient: {summary['transient']}  Permanent: {summary['permanent']}  Unknown: {summary['unknown']}"
    )
    retryable = report_data["retryable"]
    if retryable:
        console.print(
            f"\n  [cyan]Retryable ({len(retryable)}):[/cyan] {', '.join(r[:12] for r in retryable)}"
        )
    else:
        console.print("\n  No retryable entries.")
