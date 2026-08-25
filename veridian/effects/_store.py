"""Transactional SQLite permit consumption and durable dispatch outbox."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import cast

from veridian.assurance import encode_profile_v1, sha256_digest
from veridian.assurance._canonical import require_digest, require_string
from veridian.assurance._model import parse_utc_second

from ._errors import PermitError, PermitReplayError
from ._permit import ExecutionPermitV1

_SCHEMA_VERSION = 2


class OutboxStatus(StrEnum):
    """Delivery state for a dispatch intent committed with permit redemption."""

    PENDING = "pending"
    DISPATCHED = "dispatched"


@dataclass(frozen=True)
class OutboxRecord:
    """A durable, idempotent request awaiting or recording external dispatch."""

    outbox_id: str
    permit_id: str
    idempotency_key: str
    dispatch_payload: bytes
    payload_digest: str
    status: OutboxStatus
    created_at: str
    dispatched_at: str | None = None
    response_digest: str | None = None
    external_reference_digest: str | None = None
    receipt_envelope: bytes | None = None


class SqlitePermitStore:
    """Reference single-host store with atomic consume-plus-outbox semantics.

    Permit signature validation belongs to the trusted executor and must happen
    before registration/redemption. This class makes the validated payload's
    single-use transition durable and concurrency-safe.
    """

    def __init__(self, database: str | Path, *, busy_timeout_ms: int = 30_000) -> None:
        if isinstance(database, Path):
            database = str(database)
        if not database or database == ":memory:":
            raise PermitError("SqlitePermitStore requires a durable file path")
        if busy_timeout_ms <= 0:
            raise PermitError("busy_timeout_ms must be positive")
        self._database = database
        self._busy_timeout_ms = busy_timeout_ms
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(
                self._database,
                timeout=self._busy_timeout_ms / 1000,
                isolation_level=None,
            )
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
            connection.execute("PRAGMA synchronous = FULL")
            return connection
        except sqlite3.Error as exc:
            raise PermitError(f"cannot open permit store: {exc}") from exc

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS store_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS permits (
                    permit_id TEXT PRIMARY KEY,
                    permit_digest TEXT NOT NULL UNIQUE,
                    permit_bytes BLOB NOT NULL,
                    nonce TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL CHECK (status IN ('issued', 'redeemed', 'revoked')),
                    registered_at TEXT NOT NULL,
                    redemption_digest TEXT,
                    redeemed_at TEXT,
                    redemption_count INTEGER NOT NULL DEFAULT 0 CHECK (redemption_count IN (0, 1)),
                    revocation_reason TEXT,
                    revoked_at TEXT
                );
                CREATE TABLE IF NOT EXISTS effect_outbox (
                    outbox_id TEXT PRIMARY KEY,
                    permit_id TEXT NOT NULL UNIQUE REFERENCES permits(permit_id),
                    idempotency_key TEXT NOT NULL UNIQUE,
                    dispatch_payload BLOB NOT NULL,
                    payload_digest TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('pending', 'dispatched')),
                    created_at TEXT NOT NULL,
                    dispatched_at TEXT,
                    response_digest TEXT,
                    external_reference_digest TEXT,
                    receipt_envelope BLOB
                );
                CREATE INDEX IF NOT EXISTS effect_outbox_pending
                    ON effect_outbox(status, created_at, outbox_id);
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO store_metadata(key, value) VALUES ('schema_version', ?)",
                (str(_SCHEMA_VERSION),),
            )
            row = connection.execute(
                "SELECT value FROM store_metadata WHERE key = 'schema_version'"
            ).fetchone()
            if row is not None and row[0] == "1":
                columns = {
                    str(item[1])
                    for item in connection.execute("PRAGMA table_info(effect_outbox)").fetchall()
                }
                if "external_reference_digest" not in columns:
                    connection.execute(
                        "ALTER TABLE effect_outbox ADD COLUMN external_reference_digest TEXT"
                    )
                if "receipt_envelope" not in columns:
                    connection.execute("ALTER TABLE effect_outbox ADD COLUMN receipt_envelope BLOB")
                connection.execute(
                    "UPDATE store_metadata SET value = ? WHERE key = 'schema_version'",
                    (str(_SCHEMA_VERSION),),
                )
                row = (str(_SCHEMA_VERSION),)
            if row is None or row[0] != str(_SCHEMA_VERSION):
                raise PermitError("unsupported permit-store schema version")
        except sqlite3.Error as exc:
            raise PermitError(f"cannot initialize permit store: {exc}") from exc
        finally:
            connection.close()

    @staticmethod
    def _begin(connection: sqlite3.Connection) -> None:
        connection.execute("BEGIN IMMEDIATE")

    def register(self, permit: ExecutionPermitV1) -> None:
        """Persist a validated permit payload; exact retries are idempotent."""

        if not isinstance(permit, ExecutionPermitV1):
            raise PermitError("register requires an ExecutionPermitV1")
        encoded = permit.to_bytes()
        connection = self._connect()
        try:
            self._begin(connection)
            row = connection.execute(
                "SELECT permit_digest, permit_bytes FROM permits WHERE permit_id = ?",
                (permit.permit_id,),
            ).fetchone()
            if row is not None:
                if row[0] == permit.digest and bytes(row[1]) == encoded:
                    connection.commit()
                    return
                raise PermitError("conflicting permit_id is already registered")
            connection.execute(
                """
                INSERT INTO permits(
                    permit_id, permit_digest, permit_bytes, nonce, status, registered_at
                ) VALUES (?, ?, ?, ?, 'issued', ?)
                """,
                (
                    permit.permit_id,
                    permit.digest,
                    encoded,
                    permit.nonce,
                    permit.issued_at,
                ),
            )
            connection.commit()
        except PermitError:
            connection.rollback()
            raise
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise PermitError("permit nonce or digest is already registered") from exc
        except sqlite3.Error as exc:
            connection.rollback()
            raise PermitError(f"cannot register permit: {exc}") from exc
        finally:
            connection.close()

    def redeem(
        self,
        permit: ExecutionPermitV1,
        *,
        audience: str,
        current_state_digest: str,
        current_policy_digest: str,
        dispatch_payload: bytes,
        redeemed_at: str,
    ) -> OutboxRecord:
        """Atomically consume a permit and create its one durable dispatch intent."""

        if not isinstance(permit, ExecutionPermitV1):
            raise PermitError("redeem requires an ExecutionPermitV1")
        try:
            audience = require_string(audience, "audience")
            current_state_digest = require_digest(current_state_digest, "current_state_digest")
            current_policy_digest = require_digest(current_policy_digest, "current_policy_digest")
            now = parse_utc_second(redeemed_at, "redeemed_at")
        except Exception as exc:
            raise PermitError(str(exc)) from exc
        if not isinstance(dispatch_payload, bytes) or not dispatch_payload:
            raise PermitError("dispatch_payload must be non-empty bytes")
        if audience != permit.audience:
            raise PermitError("permit audience does not match this executor")
        if current_state_digest != permit.state_digest:
            raise PermitError("current state does not match permit state")
        if current_policy_digest != permit.policy_digest:
            raise PermitError("current policy does not match permit policy")
        start = parse_utc_second(permit.not_before, "permit.not_before")
        end = parse_utc_second(permit.expires_at, "permit.expires_at")
        if now < start:
            raise PermitError("permit is not yet valid")
        if now >= end:
            raise PermitError("permit has expired")

        payload_digest = sha256_digest(dispatch_payload)
        redemption_digest = sha256_digest(
            encode_profile_v1(
                {
                    "schema_id": "veridian.permit-redemption.v1",
                    "permit_digest": permit.digest,
                    "audience": audience,
                    "state_digest": current_state_digest,
                    "policy_digest": current_policy_digest,
                    "payload_digest": payload_digest,
                    "idempotency_key": permit.idempotency_key,
                }
            )
        )
        outbox_id = (
            "out_"
            + sha256_digest(f"{permit.permit_id}\n{permit.idempotency_key}".encode()).removeprefix(
                "sha256:"
            )[:32]
        )

        connection = self._connect()
        try:
            self._begin(connection)
            row = connection.execute(
                """
                SELECT permit_digest, permit_bytes, status, redemption_digest
                FROM permits WHERE permit_id = ?
                """,
                (permit.permit_id,),
            ).fetchone()
            if row is None:
                raise PermitError("permit is not registered")
            if row[0] != permit.digest or bytes(row[1]) != permit.to_bytes():
                raise PermitError("registered permit payload does not match")
            status = str(row[2])
            if status == "revoked":
                raise PermitError("permit is revoked")
            if status == "redeemed":
                if row[3] != redemption_digest:
                    raise PermitReplayError("permit replay changed the authorized dispatch")
                outbox_row = self._select_outbox(connection, outbox_id)
                if outbox_row is None:
                    raise PermitError("redeemed permit is missing its durable outbox")
                record = self._outbox_record(outbox_row)
                connection.commit()
                return record
            updated = connection.execute(
                """
                UPDATE permits
                SET status = 'redeemed', redemption_digest = ?, redeemed_at = ?,
                    redemption_count = 1
                WHERE permit_id = ? AND status = 'issued'
                """,
                (redemption_digest, redeemed_at, permit.permit_id),
            )
            if updated.rowcount != 1:
                raise PermitReplayError("permit could not be consumed exactly once")
            connection.execute(
                """
                INSERT INTO effect_outbox(
                    outbox_id, permit_id, idempotency_key, dispatch_payload,
                    payload_digest, status, created_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    outbox_id,
                    permit.permit_id,
                    permit.idempotency_key,
                    dispatch_payload,
                    payload_digest,
                    redeemed_at,
                ),
            )
            outbox_row = self._select_outbox(connection, outbox_id)
            if outbox_row is None:
                raise PermitError("outbox insert did not become visible")
            record = self._outbox_record(outbox_row)
            connection.commit()
            return record
        except (PermitError, PermitReplayError):
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise PermitError(f"cannot redeem permit: {exc}") from exc
        finally:
            connection.close()

    @staticmethod
    def _select_outbox(connection: sqlite3.Connection, outbox_id: str) -> tuple[object, ...] | None:
        row = connection.execute(
            """
            SELECT outbox_id, permit_id, idempotency_key, dispatch_payload,
                   payload_digest, status, created_at, dispatched_at, response_digest,
                   external_reference_digest, receipt_envelope
            FROM effect_outbox WHERE outbox_id = ?
            """,
            (outbox_id,),
        ).fetchone()
        return cast(tuple[object, ...] | None, row)

    @staticmethod
    def _outbox_record(row: tuple[object, ...]) -> OutboxRecord:
        try:
            dispatch_payload = row[3]
            if not isinstance(dispatch_payload, bytes):
                raise TypeError("dispatch payload is not bytes")
            return OutboxRecord(
                outbox_id=str(row[0]),
                permit_id=str(row[1]),
                idempotency_key=str(row[2]),
                dispatch_payload=dispatch_payload,
                payload_digest=str(row[4]),
                status=OutboxStatus(str(row[5])),
                created_at=str(row[6]),
                dispatched_at=None if row[7] is None else str(row[7]),
                response_digest=None if row[8] is None else str(row[8]),
                external_reference_digest=None if row[9] is None else str(row[9]),
                receipt_envelope=None
                if row[10] is None
                else SqlitePermitStore._blob(row[10], "receipt envelope"),
            )
        except (IndexError, TypeError, ValueError) as exc:
            raise PermitError("permit outbox row is corrupted") from exc

    @staticmethod
    def _blob(value: object, field_name: str) -> bytes:
        if not isinstance(value, bytes):
            raise PermitError(f"permit outbox {field_name} is corrupted")
        return value

    def pending_outbox(self, *, limit: int = 100) -> tuple[OutboxRecord, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise PermitError("limit must be a positive integer")
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT outbox_id, permit_id, idempotency_key, dispatch_payload,
                       payload_digest, status, created_at, dispatched_at, response_digest,
                       external_reference_digest, receipt_envelope
                FROM effect_outbox
                WHERE status = 'pending'
                ORDER BY created_at, outbox_id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return tuple(self._outbox_record(row) for row in rows)
        except sqlite3.Error as exc:
            raise PermitError(f"cannot read permit outbox: {exc}") from exc
        finally:
            connection.close()

    def redemption_count(self, permit_id: str) -> int:
        permit_id = require_string(permit_id, "permit_id")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT redemption_count FROM permits WHERE permit_id = ?", (permit_id,)
            ).fetchone()
            if row is None:
                raise PermitError("permit is not registered")
            return int(row[0])
        except PermitError:
            raise
        except sqlite3.Error as exc:
            raise PermitError(f"cannot read permit: {exc}") from exc
        finally:
            connection.close()

    def revoke(self, permit_id: str, *, reason: str, revoked_at: str) -> None:
        try:
            permit_id = require_string(permit_id, "permit_id")
            reason = require_string(reason, "reason")
            parse_utc_second(revoked_at, "revoked_at")
        except Exception as exc:
            raise PermitError(str(exc)) from exc
        connection = self._connect()
        try:
            self._begin(connection)
            row = connection.execute(
                "SELECT status, revocation_reason, revoked_at FROM permits WHERE permit_id = ?",
                (permit_id,),
            ).fetchone()
            if row is None:
                raise PermitError("permit is not registered")
            if row[0] == "redeemed":
                raise PermitError("redeemed permit cannot be revoked")
            if row[0] == "revoked":
                if row[1] == reason and row[2] == revoked_at:
                    connection.commit()
                    return
                raise PermitError("conflicting permit revocation")
            connection.execute(
                """
                UPDATE permits
                SET status = 'revoked', revocation_reason = ?, revoked_at = ?
                WHERE permit_id = ? AND status = 'issued'
                """,
                (reason, revoked_at, permit_id),
            )
            connection.commit()
        except PermitError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise PermitError(f"cannot revoke permit: {exc}") from exc
        finally:
            connection.close()

    def mark_dispatched(
        self,
        outbox_id: str,
        *,
        response_digest: str,
        dispatched_at: str,
        external_reference_digest: str | None = None,
        receipt_envelope: bytes | None = None,
    ) -> OutboxRecord:
        try:
            outbox_id = require_string(outbox_id, "outbox_id")
            response_digest = require_digest(response_digest, "response_digest")
            parse_utc_second(dispatched_at, "dispatched_at")
            if external_reference_digest is not None:
                external_reference_digest = require_digest(
                    external_reference_digest, "external_reference_digest"
                )
        except Exception as exc:
            raise PermitError(str(exc)) from exc
        if (external_reference_digest is None) != (receipt_envelope is None):
            raise PermitError(
                "external_reference_digest and receipt_envelope must be supplied together"
            )
        if receipt_envelope is not None and (
            not isinstance(receipt_envelope, bytes) or not receipt_envelope
        ):
            raise PermitError("receipt_envelope must be non-empty bytes")
        connection = self._connect()
        try:
            self._begin(connection)
            row = self._select_outbox(connection, outbox_id)
            if row is None:
                raise PermitError("outbox record does not exist")
            current = self._outbox_record(row)
            if current.status is OutboxStatus.DISPATCHED:
                if (
                    current.response_digest != response_digest
                    or (
                        external_reference_digest is not None
                        and current.external_reference_digest != external_reference_digest
                    )
                    or (
                        receipt_envelope is not None
                        and current.receipt_envelope != receipt_envelope
                    )
                ):
                    raise PermitError("conflicting outbox completion")
                connection.commit()
                return current
            connection.execute(
                """
                UPDATE effect_outbox
                SET status = 'dispatched', dispatched_at = ?, response_digest = ?,
                    external_reference_digest = ?, receipt_envelope = ?
                WHERE outbox_id = ? AND status = 'pending'
                """,
                (
                    dispatched_at,
                    response_digest,
                    external_reference_digest,
                    receipt_envelope,
                    outbox_id,
                ),
            )
            updated = self._select_outbox(connection, outbox_id)
            if updated is None:
                raise PermitError("outbox record disappeared")
            record = self._outbox_record(updated)
            connection.commit()
            return record
        except PermitError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise PermitError(f"cannot update permit outbox: {exc}") from exc
        finally:
            connection.close()
