"""
veridian.observability.dashboard
─────────────────────────────────
VeridianDashboard — FastAPI SSE live dashboard on port 7474.

Rules:
- Port 7474. Not 8080. Not 7860.
- FastAPI is an optional dependency ([dashboard] extra). Import guard required.
- Serves Server-Sent Events (SSE) stream of trace events from a JSONL tail.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

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

    def __init__(
        self,
        trace_file: Path | None = None,
        port: int = DASHBOARD_PORT,
        host: str = "127.0.0.1",
    ) -> None:
        self._trace_file = trace_file or Path("veridian_trace.jsonl")
        self._port = port
        self._host = host
        self._app: Any = None

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
            version="0.1.0",
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

        @app.get("/events")  # type: ignore[misc]
        async def events() -> StreamingResponse:
            """SSE endpoint streaming live trace events."""
            return StreamingResponse(
                _sse_generator(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        @app.get("/health")  # type: ignore[misc]
        async def health() -> dict[str, str]:
            """Health check endpoint."""
            return {"status": "ok", "port": str(self._port)}

        @app.get("/")  # type: ignore[misc]
        async def index() -> dict[str, Any]:
            """Dashboard info."""
            return {
                "name": "Veridian Dashboard",
                "port": self._port,
                "trace_file": str(trace_file),
                "events_endpoint": "/events",
            }

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
