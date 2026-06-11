"""
veridian.ledger.wal
---------------------------------------------------------------
Append-only write-ahead log for TaskLedger (opt-in, experimental).

Enabled via ``VERIDIAN_LEDGER_WAL=1``. Each ledger state transition appends
one newline-terminated JSON line ``{"seq": n, "tasks": [task_dict, ...]}``
(flush + fsync) instead of rewriting the whole snapshot, making write cost
O(entry) instead of O(ledger). Replay applies entries as upserts onto the
snapshot, so re-applying any suffix of the log is idempotent --- this is what
makes the snapshot-then-truncate compaction order crash-safe (ARIES-style
redo logging).

Recovery rules:
* A trailing line that is torn (no newline), unparseable, or breaks the
  strictly-increasing ``seq`` chain marks the end of the valid log. It is an
  expected crash artifact, never an error.
* Readers simply ignore the invalid tail. Writers MUST call
  :meth:`WalLog.repair` (under the ledger file lock) before appending,
  otherwise a valid line written after a torn fragment would be unreachable
  on replay.

Known limitation (shared with the snapshot rename path): the parent
directory entry is not fsynced, so the very first append after file creation
is not guaranteed durable across power loss on all filesystems.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from veridian.core.atomic_io import _fsync_enabled

log = logging.getLogger(__name__)

__all__ = ["WalLog", "WalReplay"]


class WalReplay:
    """Result of scanning the log: valid upserts and where validity ends."""

    def __init__(
        self,
        upserts: list[dict[str, Any]],
        last_seq: int,
        entry_count: int,
        valid_end_offset: int,
        file_size: int,
    ) -> None:
        self.upserts = upserts
        self.last_seq = last_seq
        self.entry_count = entry_count
        self.valid_end_offset = valid_end_offset
        self.file_size = file_size

    @property
    def has_invalid_tail(self) -> bool:
        return self.file_size > self.valid_end_offset


class WalLog:
    """Newline-delimited JSON log with torn-tail-tolerant replay."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def size(self) -> int:
        """Current file size in bytes (0 when absent). Used as a cache stamp."""
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    def replay(self) -> WalReplay:
        """Scan the log, returning valid upserts in order.

        Stops at the first line that is torn, unparseable, not an object of
        the expected shape, or whose ``seq`` is not exactly ``previous + 1``.
        """
        try:
            blob = self.path.read_bytes()
        except OSError:
            return WalReplay([], 0, 0, 0, 0)

        upserts: list[dict[str, Any]] = []
        last_seq = 0
        entries = 0
        offset = 0
        for line in blob.split(b"\n"):
            line_end = offset + len(line) + 1  # +1 for the newline
            if line_end > len(blob):
                break  # torn final line: no terminating newline
            if not line.strip():
                offset = line_end
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                break
            if (
                not isinstance(entry, dict)
                or entry.get("seq") != last_seq + 1
                or not isinstance(entry.get("tasks"), list)
            ):
                break
            for task_dict in entry["tasks"]:
                if isinstance(task_dict, dict) and isinstance(task_dict.get("id"), str):
                    upserts.append(task_dict)
            last_seq = entry["seq"]
            entries += 1
            offset = line_end

        return WalReplay(upserts, last_seq, entries, offset, len(blob))

    def repair(self, replay: WalReplay) -> None:
        """Truncate an invalid tail. Caller must hold the ledger file lock."""
        if not replay.has_invalid_tail:
            return
        with open(self.path, "r+b") as f:
            f.truncate(replay.valid_end_offset)
            f.flush()
            if _fsync_enabled():
                try:
                    os.fsync(f.fileno())
                except OSError as exc:
                    log.warning("wal.fsync_failed path=%s err=%s", self.path, exc)
        log.warning(
            "wal.repaired_torn_tail path=%s kept=%d dropped=%d bytes",
            self.path,
            replay.valid_end_offset,
            replay.file_size - replay.valid_end_offset,
        )

    def append(self, line: str) -> None:
        """Append one pre-serialized, newline-terminated entry durably.

        Caller must hold the ledger file lock and must have repaired any
        invalid tail first.
        """
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            if _fsync_enabled():
                try:
                    os.fsync(f.fileno())
                except OSError as exc:
                    log.warning(
                        "wal.fsync_failed path=%s err=%s (append proceeds, not power-loss durable)",
                        self.path,
                        exc,
                    )

    def truncate(self) -> None:
        """Empty the log after a snapshot. Caller must hold the file lock.

        Crash-safety: the snapshot is renamed into place BEFORE this runs,
        and replay is idempotent upserts, so a crash that preserves the old
        log on top of the new snapshot re-derives the identical state.
        """
        with open(self.path, "w", encoding="utf-8") as f:
            f.flush()
            if _fsync_enabled():
                try:
                    os.fsync(f.fileno())
                except OSError as exc:
                    log.warning("wal.fsync_failed path=%s err=%s", self.path, exc)
