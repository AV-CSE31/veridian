"""Veridian CLI — Rich-based terminal interface for managing agent verification runs."""
from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from veridian.ledger.ledger import TaskLedger

app = typer.Typer(
    name="veridian",
    help="Deterministic verification infrastructure for autonomous AI agents.",
    no_args_is_help=True,
)
console = Console()

# ---------------------------------------------------------------------------
# Shared options
# ---------------------------------------------------------------------------

LEDGER_OPT = typer.Option("ledger.json", "--ledger", "-l", help="Path to ledger.json")


def _load_ledger(ledger_path: str) -> TaskLedger:
    """Load a TaskLedger from the given path, or exit with error."""
    p = Path(ledger_path)
    if not p.exists():
        console.print(f"[red]Error:[/red] Ledger not found at {p}")
        raise typer.Exit(code=1)
    return TaskLedger(path=p)


# ---------------------------------------------------------------------------
# veridian --version
# ---------------------------------------------------------------------------

def _version_callback(value: bool) -> None:
    if value:
        from veridian import __version__
        console.print(f"veridian {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", "-V", callback=_version_callback, is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """Veridian — deterministic verification for autonomous AI agents."""


# ---------------------------------------------------------------------------
# veridian init
# ---------------------------------------------------------------------------

@app.command()
def init(
    ledger: str = LEDGER_OPT,
) -> None:
    """Initialize a new Veridian ledger."""
    p = Path(ledger)
    if p.exists():
        console.print(f"[red]Error:[/red] Ledger already exists at {p}")
        raise typer.Exit(code=1)

    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"tasks": []}, indent=2))
    console.print(f"[green]Created[/green] {p}")


# ---------------------------------------------------------------------------
# veridian status
# ---------------------------------------------------------------------------

@app.command()
def status(
    ledger: str = LEDGER_OPT,
) -> None:
    """Show ledger statistics."""
    led = _load_ledger(ledger)
    stats = led.stats()

    table = Table(title="Ledger Status", show_header=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="bold")

    table.add_row("Total tasks", str(stats.total))
    table.add_row("Done", str(stats.done))
    table.add_row("Pending", str(stats.pending))
    table.add_row("In progress", str(stats.in_progress))
    table.add_row("Failed", str(stats.failed))
    table.add_row("% Complete", f"{stats.pct_complete:.0f}%")
    table.add_row("Retry rate", f"{stats.retry_rate:.1%}")
    table.add_row("Tokens used", f"{stats.total_tokens_used:,}")
    table.add_row("Est. cost", f"${stats.estimated_cost_usd:.4f}")

    console.print(table)


# ---------------------------------------------------------------------------
# veridian list
# ---------------------------------------------------------------------------

@app.command(name="list")
def list_tasks(
    ledger: str = LEDGER_OPT,
    status_filter: str | None = typer.Option(
        None, "--status", "-s", help="Filter by status (pending, done, failed, etc.)",
    ),
) -> None:
    """List tasks in the ledger."""
    led = _load_ledger(ledger)
    tasks = led.list(status=status_filter)

    if not tasks:
        console.print("[dim]No tasks found.[/dim]")
        return

    table = Table(show_header=True)
    table.add_column("ID", style="cyan", max_width=20)
    table.add_column("Title", max_width=40)
    table.add_column("Status", style="bold")
    table.add_column("Verifier")
    table.add_column("Attempt", justify="right")

    for t in tasks:
        status_style = {
            "done": "green",
            "failed": "red",
            "in_progress": "yellow",
            "pending": "dim",
            "skipped": "dim",
            "abandoned": "red dim",
        }.get(t.status.value if hasattr(t.status, "value") else str(t.status), "")

        table.add_row(
            t.id,
            t.title,
            f"[{status_style}]{t.status.value}[/{status_style}]",
            t.verifier_id,
            str(t.retry_count),
        )

    console.print(table)


# ---------------------------------------------------------------------------
# veridian gc
# ---------------------------------------------------------------------------

@app.command()
def gc(
    ledger: str = LEDGER_OPT,
) -> None:
    """Run entropy consistency checks (read-only)."""
    from veridian.entropy import EntropyGC

    led = _load_ledger(ledger)
    entropy = EntropyGC(ledger=led, report_path=Path(ledger).parent / "entropy_report.md")
    issues = entropy.run()

    if not issues:
        console.print("[green]No issues found.[/green] Ledger is consistent.")
        return

    table = Table(title=f"Entropy Report — {len(issues)} issue(s)", show_header=True)
    table.add_column("Type", style="red")
    table.add_column("Task ID", style="cyan")
    table.add_column("Detail")

    for issue in issues:
        table.add_row(
            issue.issue_type.value,
            issue.task_id,
            issue.detail,
        )

    console.print(table)


# ---------------------------------------------------------------------------
# veridian reset
# ---------------------------------------------------------------------------

@app.command()
def reset(
    ledger: str = LEDGER_OPT,
    confirm: bool = typer.Option(False, "--confirm", help="Skip confirmation prompt."),
) -> None:
    """Reset all failed tasks back to pending. Requires --confirm."""
    if not confirm:
        console.print("[yellow]This will reset all failed tasks to pending.[/yellow]")
        console.print("Run with [bold]--confirm[/bold] to proceed.")
        return

    led = _load_ledger(ledger)
    count = led.reset_failed()
    console.print(f"[green]Reset {count} failed task(s) to pending.[/green]")


# ---------------------------------------------------------------------------
# veridian skip
# ---------------------------------------------------------------------------

@app.command()
def skip(
    ledger: str = LEDGER_OPT,
    task_id: str = typer.Option(..., "--task-id", "-t", help="Task ID to skip."),
    reason: str = typer.Option("", "--reason", "-r", help="Reason for skipping."),
    confirm: bool = typer.Option(False, "--confirm", help="Skip confirmation prompt."),
) -> None:
    """Skip a task. Requires --confirm."""
    if not confirm:
        console.print(f"[yellow]This will skip task '{task_id}'.[/yellow]")
        console.print("Run with [bold]--confirm[/bold] to proceed.")
        return

    led = _load_ledger(ledger)
    try:
        led.skip(task_id, reason=reason)
        console.print(f"[green]Skipped task '{task_id}'.[/green]")
    except Exception as e:
        console.print(f"[red]Error:[/red] Task not found: {task_id}")
        raise typer.Exit(code=1) from e


# ---------------------------------------------------------------------------
# veridian retry
# ---------------------------------------------------------------------------

@app.command()
def retry(
    ledger: str = LEDGER_OPT,
    task_id: str = typer.Option(..., "--task-id", "-t", help="Task ID to retry."),
    confirm: bool = typer.Option(False, "--confirm", help="Skip confirmation prompt."),
) -> None:
    """Retry a failed task. Requires --confirm."""
    if not confirm:
        console.print(f"[yellow]This will retry task '{task_id}'.[/yellow]")
        console.print("Run with [bold]--confirm[/bold] to proceed.")
        return

    led = _load_ledger(ledger)
    try:
        led.reset_failed(task_ids=[task_id])
        console.print(f"[green]Reset task '{task_id}' to pending.[/green]")
    except Exception as e:
        console.print(f"[red]Error:[/red] Task not found: {task_id}")
        raise typer.Exit(code=1) from e


# ---------------------------------------------------------------------------
# veridian run
# ---------------------------------------------------------------------------

@app.command()
def run(
    ledger: str = LEDGER_OPT,
    model: str = typer.Option("", "--model", "-m", help="LLM model name."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Assemble context but don't execute."),
    max_parallel: int = typer.Option(1, "--parallel", "-p", help="Max parallel tasks."),
) -> None:
    """Run pending tasks through verification pipeline."""
    from veridian.core.config import VeridianConfig
    from veridian.hooks.registry import HookRegistry
    from veridian.loop.runner import VeridianRunner
    from veridian.providers.mock_provider import MockProvider
    from veridian.verify.base import VerifierRegistry

    led = _load_ledger(ledger)
    config = VeridianConfig(
        dry_run=dry_run,
        max_parallel=max_parallel,
    )
    if model:
        config.model = model

    from veridian.providers.base import LLMProvider

    # Use MockProvider for dry-run; real provider requires litellm
    provider: LLMProvider
    if dry_run:
        provider = MockProvider()
    else:
        try:
            from veridian.providers.litellm_provider import LiteLLMProvider
            provider = LiteLLMProvider(model=config.model)
        except ImportError as exc:
            console.print(
                "[red]Error:[/red] LLM provider not installed. "
                "Run: pip install veridian-ai[llm]"
            )
            raise typer.Exit(code=1) from exc

    runner_inst = VeridianRunner(
        ledger=led,
        provider=provider,
        config=config,
        hooks=HookRegistry(),
        verifier_registry=VerifierRegistry(),
    )

    summary = runner_inst.run()

    table = Table(title="Run Summary", show_header=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="bold")

    table.add_row("Run ID", summary.run_id)
    table.add_row("Dry run", str(summary.dry_run))
    table.add_row("Done", str(summary.done_count))
    table.add_row("Failed", str(summary.failed_count))
    table.add_row("Total", str(summary.total_tasks))
    table.add_row("Duration", f"{summary.duration_seconds:.1f}s")

    console.print(table)

    if summary.errors:
        console.print("\n[red]Errors:[/red]")
        for err in summary.errors:
            console.print(f"  - {err}")
