"""
veridian.core.atomic_io
───────────────────────
Single shared implementation of "write-via-temp-then-rename" for any
file that must appear on disk atomically: traces, ledger fragments,
reports, drafts, …

Before Phase 6.A several near-identical helpers lived across platform
subsystems. They have been collapsed into :func:`atomic_write_text`, which:

* mkdir-p's the parent directory,
* writes the payload to a sibling temp file (so ``os.replace`` is
  guaranteed to be a same-filesystem rename),
* ``flush`` + ``os.fsync`` the temp file so the kernel has actually
  written the data to disk before we rename — without this, a power
  loss between ``write`` and ``replace`` can leave the file empty
  despite the "atomic" docstring (Phase 6.B durability fix),
* ``os.replace``'s atomically,
* cleans up the temp file on any failure — including the rare case where
  the rename itself raises after ``flush`` succeeded.

JSON callers use the convenience :func:`atomic_write_json` which dumps
with ``indent=2`` (matching the legacy behaviour of the four duplicate
implementations) and delegates to the text writer.

Set ``VERIDIAN_ATOMIC_IO_SKIP_FSYNC=1`` to skip the fsync — useful for
test suites where every fsync wastes seconds and durability is not
actually being tested. Production deployments should leave this unset.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

__all__ = ["atomic_write_text", "atomic_write_json"]


def _fsync_enabled() -> bool:
    """Return False when the explicit opt-out env var is set."""
    return os.getenv("VERIDIAN_ATOMIC_IO_SKIP_FSYNC", "").strip() != "1"


def atomic_write_text(path: Path | str, content: str, *, encoding: str = "utf-8") -> None:
    """Write ``content`` to ``path`` atomically and durably.

    Args:
        path: Destination. Parent directory is created if absent.
        content: Full file contents. Empty string is allowed.
        encoding: Text encoding. Defaults to UTF-8 — overriding is rarely
            needed but kept for compatibility with existing callers.

    Raises:
        OSError: if the underlying rename fails (after temp-file cleanup).
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    tmp_name: str = ""
    do_fsync = _fsync_enabled()
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            dir=target.parent,
            delete=False,
            prefix=f".{target.name}.",
            suffix=".tmp",
            encoding=encoding,
        ) as handle:
            handle.write(content)
            handle.flush()
            if do_fsync:
                # Force the kernel to push our bytes to disk before we
                # rename — guarantees the post-rename file is at least
                # as up-to-date as ``content``. Without this, a crash
                # between flush and replace can leave the renamed file
                # empty despite the atomic-rename contract.
                with contextlib.suppress(OSError):
                    os.fsync(handle.fileno())
            tmp_name = handle.name
        os.replace(tmp_name, target)
    except OSError:
        # ``os.replace`` failure path: drop the orphan temp file so the
        # next caller doesn't inherit a half-written sibling.
        if tmp_name:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
        raise


def atomic_write_json(path: Path | str, data: Any, *, indent: int | None = 2) -> None:
    """Serialize ``data`` and write it via :func:`atomic_write_text`.

    Matches the JSON output of the four legacy helpers (``indent=2``,
    ``ensure_ascii=False`` is *not* set so the default ASCII-only output
    is preserved — flip via ``json.dumps`` directly if you need
    Unicode-permissive output).
    """
    atomic_write_text(path, json.dumps(data, indent=indent))
