"""Checksummed append-only storage for :class:`~veridian.ledger.TaskLedger`.

The WAL is deliberately small: it records complete task upserts, not Python
objects or arbitrary commands.  A completed append is flushed and fsynced
before the caller can acknowledge the ledger mutation.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from veridian.core.exceptions import LedgerCorrupted

WAL_FORMAT_VERSION = 1
GENESIS_HASH = f"sha256:{'0' * 64}"
_WAL_RECORD_FIELDS = {
    "version",
    "ledger_id",
    "generation",
    "seq",
    "previous_hash",
    "tasks",
    "checksum",
}
_WAL_HEAD_FIELDS = {
    "version",
    "ledger_id",
    "generation",
    "last_seq",
    "last_hash",
    "checksum",
}

__all__ = [
    "GENESIS_HASH",
    "WAL_FORMAT_VERSION",
    "WalHead",
    "WalHeadStore",
    "WalLog",
    "WalReplay",
]


def _replace_file(source: str | Path, target: Path) -> None:
    last_error: PermissionError | None = None
    for attempt in range(5):
        try:
            os.replace(source, target)
            return
        except PermissionError as exc:
            last_error = exc
            if attempt < 4:
                time.sleep(0.01 * (attempt + 1))
    assert last_error is not None
    raise last_error


def _sync_directory(path: Path, *, fsync: bool) -> None:
    """Persist a directory-entry change where the OS exposes that primitive."""
    if not fsync or os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _canonical_bytes(value: object) -> bytes:
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise LedgerCorrupted(f"WAL value is not canonical JSON: {exc}") from exc
    return text.encode("utf-8")


def _digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


@dataclass(frozen=True, slots=True)
class WalReplay:
    """Validated prefix of a WAL and any physically incomplete tail."""

    upserts: tuple[dict[str, Any], ...]
    ledger_id: str | None
    generation: int | None
    last_seq: int
    last_hash: str
    hashes: tuple[str, ...]
    entry_end_offsets: tuple[int, ...]
    upsert_sequences: tuple[int, ...]
    entry_count: int
    valid_end_offset: int
    file_size: int
    invalid_tail_reason: str | None = None

    @property
    def has_invalid_tail(self) -> bool:
        return self.file_size > self.valid_end_offset


class WalLog:
    """A newline-delimited, hash-chained task-upsert log."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def size(self) -> int:
        try:
            return self.path.stat().st_size
        except FileNotFoundError:
            return 0
        except OSError as exc:
            raise LedgerCorrupted(f"cannot inspect WAL {self.path}: {exc}") from exc

    def replay(
        self,
        *,
        expected_ledger_id: str | None = None,
        expected_generation: int | None = None,
    ) -> WalReplay:
        """Validate and replay the log.

        Only an incomplete/unparseable final record is treated as a crash
        tail.  A complete record with a wrong checksum, identity, sequence, or
        hash link is corruption and fails closed.
        """
        try:
            blob = self.path.read_bytes()
        except FileNotFoundError:
            blob = b""
        except OSError as exc:
            raise LedgerCorrupted(f"cannot read WAL {self.path}: {exc}") from exc

        upserts: list[dict[str, Any]] = []
        ledger_id = expected_ledger_id
        generation = expected_generation
        last_seq = 0
        last_hash = GENESIS_HASH
        entries = 0
        hashes: list[str] = []
        entry_end_offsets: list[int] = []
        upsert_sequences: list[int] = []
        offset = 0
        invalid_tail_reason: str | None = None

        for raw_line in blob.splitlines(keepends=True):
            if not raw_line.endswith(b"\n"):
                invalid_tail_reason = "unterminated final record"
                break
            line_end = offset + len(raw_line)
            encoded = raw_line[:-1]
            try:
                entry = json.loads(encoded)
            except (UnicodeDecodeError, json.JSONDecodeError):
                invalid_tail_reason = "unparseable final record"
                break
            if not isinstance(entry, dict):
                raise LedgerCorrupted("WAL record must be an object")

            record_ledger_id = entry.get("ledger_id")
            record_generation = entry.get("generation")
            seq = entry.get("seq")
            tasks = entry.get("tasks")
            checksum = entry.get("checksum")
            if (
                set(entry) != _WAL_RECORD_FIELDS
                or entry.get("version") != WAL_FORMAT_VERSION
                or not isinstance(record_ledger_id, str)
                or not record_ledger_id
                or not isinstance(record_generation, int)
                or isinstance(record_generation, bool)
                or record_generation < 1
                or not isinstance(seq, int)
                or isinstance(seq, bool)
                or not isinstance(tasks, list)
                or not tasks
                or not isinstance(checksum, str)
            ):
                raise LedgerCorrupted("WAL record has an invalid schema")
            if ledger_id is None:
                ledger_id = record_ledger_id
            if generation is None:
                generation = record_generation
            if record_ledger_id != ledger_id or record_generation != generation:
                raise LedgerCorrupted("WAL record is bound to a different ledger generation")
            if seq != last_seq + 1:
                raise LedgerCorrupted(
                    f"WAL sequence discontinuity: expected {last_seq + 1}, got {seq}"
                )
            if entry.get("previous_hash") != last_hash:
                raise LedgerCorrupted("WAL hash chain is broken")

            unsigned = dict(entry)
            del unsigned["checksum"]
            if checksum != _digest(unsigned):
                raise LedgerCorrupted("WAL record checksum mismatch")

            task_ids: set[str] = set()
            for task_dict in tasks:
                if (
                    not isinstance(task_dict, dict)
                    or not isinstance(task_dict.get("id"), str)
                    or not task_dict["id"]
                ):
                    raise LedgerCorrupted("WAL contains an invalid task upsert")
                if task_dict["id"] in task_ids:
                    raise LedgerCorrupted("WAL record contains duplicate task upserts")
                task_ids.add(task_dict["id"])
                upserts.append(dict(task_dict))
                upsert_sequences.append(seq)

            last_seq = seq
            last_hash = checksum
            hashes.append(checksum)
            entry_end_offsets.append(line_end)
            entries += 1
            offset = line_end

        return WalReplay(
            upserts=tuple(upserts),
            ledger_id=ledger_id,
            generation=generation,
            last_seq=last_seq,
            last_hash=last_hash,
            hashes=tuple(hashes),
            entry_end_offsets=tuple(entry_end_offsets),
            upsert_sequences=tuple(upsert_sequences),
            entry_count=entries,
            valid_end_offset=offset,
            file_size=len(blob),
            invalid_tail_reason=invalid_tail_reason,
        )

    def append(
        self,
        tasks: list[dict[str, Any]],
        *,
        ledger_id: str,
        generation: int,
        seq: int,
        previous_hash: str,
        fsync: bool,
    ) -> str:
        """Append and durably flush one record, returning its checksum."""
        unsigned: dict[str, object] = {
            "version": WAL_FORMAT_VERSION,
            "ledger_id": ledger_id,
            "generation": generation,
            "seq": seq,
            "previous_hash": previous_hash,
            "tasks": tasks,
        }
        checksum = _digest(unsigned)
        record = dict(unsigned)
        record["checksum"] = checksum
        encoded = _canonical_bytes(record) + b"\n"

        self.path.parent.mkdir(parents=True, exist_ok=True)
        existed = self.path.exists()
        with self.path.open("ab") as handle:
            handle.write(encoded)
            handle.flush()
            if fsync:
                os.fsync(handle.fileno())
        if not existed:
            _sync_directory(self.path.parent, fsync=fsync)
        return checksum

    def repair(self, replay: WalReplay, *, fsync: bool) -> None:
        """Drop only the physically incomplete tail identified by replay()."""
        if not replay.has_invalid_tail:
            return
        try:
            with self.path.open("r+b") as handle:
                handle.truncate(replay.valid_end_offset)
                handle.flush()
                if fsync:
                    os.fsync(handle.fileno())
        except OSError:
            raise

    def truncate_to_sequence(self, replay: WalReplay, sequence: int, *, fsync: bool) -> None:
        """Discard valid but unanchored records after ``sequence``."""
        if sequence < 0 or sequence > replay.last_seq:
            raise LedgerCorrupted("WAL truncation sequence is outside the validated log")
        end_offset = 0 if sequence == 0 else replay.entry_end_offsets[sequence - 1]
        with self.path.open("r+b") as handle:
            handle.truncate(end_offset)
            handle.flush()
            if fsync:
                os.fsync(handle.fileno())

    def sealed_path(self, *, generation: int, last_hash: str) -> Path:
        suffix = last_hash.removeprefix("sha256:")[:16]
        return self.path.with_name(f"{self.path.name}.g{generation:08d}.{suffix}.sealed")

    def seal(self, replay: WalReplay, *, fsync: bool) -> Path:
        """Move a fully validated generation to an immutable archive."""
        if replay.entry_count == 0 or replay.generation is None:
            raise LedgerCorrupted("cannot seal an empty WAL generation")
        target = self.sealed_path(generation=replay.generation, last_hash=replay.last_hash)
        if target.exists():
            if not self.path.exists() or target.read_bytes() != self.path.read_bytes():
                raise LedgerCorrupted("ambiguous sealed WAL generation")
            self.path.unlink()
        else:
            _replace_file(self.path, target)
        _sync_directory(self.path.parent, fsync=fsync)
        return target

    def reset(self, *, fsync: bool) -> None:
        """Atomically create an empty current-generation WAL."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.flush()
                if fsync:
                    os.fsync(handle.fileno())
            _replace_file(tmp_name, self.path)
            _sync_directory(self.path.parent, fsync=fsync)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise


@dataclass(frozen=True, slots=True)
class WalHead:
    """Durable local anchor for single-artifact rollback detection.

    A coordinated rollback of both the log and this sidecar requires an
    application-controlled external checkpoint to detect.
    """

    ledger_id: str
    generation: int
    last_seq: int
    last_hash: str


class WalHeadStore:
    """Atomic sidecar recording the latest acknowledged WAL position."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> WalHead | None:
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise LedgerCorrupted(f"cannot read WAL head {self.path}: {exc}") from exc
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LedgerCorrupted(f"WAL head is malformed: {exc}") from exc
        if (
            not isinstance(payload, dict)
            or set(payload) != _WAL_HEAD_FIELDS
            or not isinstance(payload.get("checksum"), str)
        ):
            raise LedgerCorrupted("WAL head has an invalid schema")
        checksum = payload["checksum"]
        unsigned = dict(payload)
        del unsigned["checksum"]
        if checksum != _digest(unsigned):
            raise LedgerCorrupted("WAL head checksum mismatch")
        if (
            payload.get("version") != WAL_FORMAT_VERSION
            or not isinstance(payload.get("ledger_id"), str)
            or not payload["ledger_id"]
            or not isinstance(payload.get("generation"), int)
            or isinstance(payload["generation"], bool)
            or payload["generation"] < 1
            or not isinstance(payload.get("last_seq"), int)
            or isinstance(payload["last_seq"], bool)
            or payload["last_seq"] < 0
            or not isinstance(payload.get("last_hash"), str)
        ):
            raise LedgerCorrupted("WAL head has an invalid schema")
        return WalHead(
            ledger_id=payload["ledger_id"],
            generation=payload["generation"],
            last_seq=payload["last_seq"],
            last_hash=payload["last_hash"],
        )

    def write(self, head: WalHead, *, fsync: bool) -> None:
        unsigned: dict[str, object] = {
            "version": WAL_FORMAT_VERSION,
            "ledger_id": head.ledger_id,
            "generation": head.generation,
            "last_seq": head.last_seq,
            "last_hash": head.last_hash,
        }
        payload = dict(unsigned)
        payload["checksum"] = _digest(unsigned)
        encoded = _canonical_bytes(payload)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                if fsync:
                    os.fsync(handle.fileno())
            _replace_file(tmp_name, self.path)
            _sync_directory(self.path.parent, fsync=fsync)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise
