"""
veridian.observability.dashboard
─────────────────────────────────
VeridianDashboard — FastAPI SSE live dashboard on port 7474.

Rules:
- Port 7474. Not 8080. Not 7860.
- FastAPI is an optional dependency ([dashboard] extra). Import guard required.
- Serves Server-Sent Events (SSE) stream of trace events from a JSONL tail.
- GET /slo returns SLO compliance report as JSON.
- GET /alerts returns recent alerts as JSON list.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import deque
from collections.abc import AsyncGenerator
from dataclasses import asdict
from pathlib import Path
from typing import Any

from veridian.observability.alerts import Alert, AlertManager
from veridian.observability.slo import BUILTIN_SLOS, SLOEvaluator


def _verifier_registry_loaded() -> bool:
    """Best-effort probe: have any built-in verifiers self-registered?"""
    try:
        from veridian.verify.base import registry

        return bool(getattr(registry, "_classes", {}))
    except Exception:
        return False


log = logging.getLogger(__name__)

__all__ = ["VeridianDashboard", "DASHBOARD_PORT"]

DASHBOARD_PORT: int = 7474


class VeridianDashboard:
    """
    Live SSE dashboard backed by veridian_trace.jsonl.

    Requires the `dashboard` extra: ``pip install veridian-ai[dashboard]``.

    Usage::

        dashboard = VeridianDashboard(trace_file=Path("veridian_trace.jsonl"))
        dashboard.serve()   # blocks; runs on port 7474
    """

    # Bound the recent-alerts buffer so a long-running dashboard process
    # does not grow unbounded on a noisy task feed.
    RECENT_ALERTS_MAX: int = 1000

    def __init__(
        self,
        trace_file: Path | None = None,
        port: int = DASHBOARD_PORT,
        host: str = "127.0.0.1",
        slo_evaluator: SLOEvaluator | None = None,
        alert_manager: AlertManager | None = None,
        ledger_path: Path | None = None,
        recent_alerts_max: int | None = None,
    ) -> None:
        self._trace_file = trace_file or Path("veridian_trace.jsonl")
        self._port = port
        self._host = host
        self._app: Any = None
        self._slo_evaluator = slo_evaluator or SLOEvaluator(definitions=BUILTIN_SLOS)
        self._alert_manager = alert_manager
        self._latest_metrics: dict[str, float] = {}
        # ``deque(maxlen=…)`` evicts the oldest entries automatically so the
        # buffer cannot exceed ``RECENT_ALERTS_MAX`` (or the constructor
        # override). Existing serialisations that iterate ``_recent_alerts``
        # continue to work because deque is iterable in insertion order.
        max_alerts = recent_alerts_max if recent_alerts_max is not None else self.RECENT_ALERTS_MAX
        self._recent_alerts: deque[Alert] = deque(maxlen=max_alerts)
        # Optional: if supplied, /ready probes the ledger file's reachability
        # so a k8s readiness probe goes 503 → 200 only when persistence is
        # actually addressable, not just when the FastAPI process is up.
        self._ledger_path = ledger_path

    def _build_app(self) -> Any:
        """Build and return the FastAPI application."""
        try:
            from fastapi import FastAPI
            from fastapi.responses import StreamingResponse
        except ImportError as exc:
            raise ImportError(
                "FastAPI is required for the dashboard. "
                "Install it with: pip install veridian-ai[dashboard]"
            ) from exc

        app = FastAPI(
            title="Veridian Dashboard",
            description="Live SSE stream of Veridian trace events.",
            version="4.0.0",
        )

        trace_file = self._trace_file

        async def _sse_generator() -> AsyncGenerator[str, None]:
            """Tail the JSONL trace file and emit SSE events."""
            last_pos = 0
            while True:
                if trace_file.exists():
                    with trace_file.open("r", encoding="utf-8") as fh:
                        fh.seek(last_pos)
                        for line in fh:
                            line = line.strip()
                            if line:
                                try:
                                    data = json.loads(line)
                                    yield f"data: {json.dumps(data)}\n\n"
                                except json.JSONDecodeError:
                                    pass
                        last_pos = fh.tell()
                await asyncio.sleep(0.5)

        @app.get("/events")
        async def events() -> StreamingResponse:
            """SSE endpoint streaming live trace events."""
            return StreamingResponse(
                _sse_generator(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        @app.get("/health")
        async def health() -> dict[str, str]:
            """Shallow liveness — returns 200 as long as the process is up."""
            return {"status": "ok", "port": str(self._port)}

        from fastapi import HTTPException

        ledger_path = self._ledger_path
        verifier_registry_ready = _verifier_registry_loaded

        @app.get("/ready")
        async def ready() -> dict[str, Any]:
            """Deep readiness probe for Kubernetes / autoscalers.

            Returns 200 iff:

            * the verifier registry has been initialised (built-ins loaded),
            * the ledger file (when configured) exists and is readable.

            Returns 503 with a structured error body otherwise so the
            scheduler can keep the pod out of rotation until persistence is
            actually addressable.
            """
            failures: list[dict[str, str]] = []
            if not verifier_registry_ready():
                failures.append({"check": "verifier_registry", "reason": "no verifiers registered"})
            if ledger_path is not None:
                try:
                    if not ledger_path.exists():
                        failures.append({"check": "ledger", "reason": f"missing: {ledger_path}"})
                    elif not os.access(ledger_path, os.R_OK):
                        failures.append({"check": "ledger", "reason": f"unreadable: {ledger_path}"})
                except Exception as exc:
                    failures.append({"check": "ledger", "reason": str(exc)})
            if failures:
                raise HTTPException(status_code=503, detail={"not_ready": failures})
            return {"status": "ready"}

        @app.get("/")
        async def index() -> dict[str, Any]:
            """Dashboard info."""
            return {
                "name": "Veridian Dashboard",
                "port": self._port,
                "trace_file": str(trace_file),
                "events_endpoint": "/events",
            }

        slo_evaluator = self._slo_evaluator
        dashboard_self = self

        @app.get("/slo")
        async def slo_report() -> list[dict[str, Any]]:
            """Return SLO compliance report as JSON."""
            reports = slo_evaluator.evaluate(dashboard_self._latest_metrics)
            return [asdict(r) for r in reports]

        @app.get("/alerts")
        async def recent_alerts() -> list[dict[str, Any]]:
            """Return recent alerts as JSON list."""
            return [asdict(a) for a in dashboard_self._recent_alerts]

        @app.get("/metrics")
        async def metrics() -> Any:
            """OpenMetrics exposition for Prometheus / Grafana scrapers."""
            from fastapi.responses import PlainTextResponse  # noqa: PLC0415

            from veridian.observability.metrics import (  # noqa: PLC0415
                default_registry,
                render_openmetrics,
            )

            return PlainTextResponse(
                content=render_openmetrics(default_registry()),
                media_type="text/plain; version=0.0.4; charset=utf-8",
            )

        return app

    @property
    def app(self) -> Any:
        """Return the FastAPI application (lazy-built)."""
        if self._app is None:
            self._app = self._build_app()
        return self._app

    def serve(self) -> None:
        """Start the uvicorn server. Blocks until interrupted."""
        try:
            import uvicorn
        except ImportError as exc:
            raise ImportError(
                "uvicorn is required for the dashboard. "
                "Install it with: pip install veridian-ai[dashboard]"
            ) from exc

        log.info("Starting Veridian dashboard on http://%s:%d", self._host, self._port)
        uvicorn.run(self.app, host=self._host, port=self._port, log_level="info")
