from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

RUNTIME_SCHEMA_VERSION = 2


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class SQLiteRuntimeStore:
    """Versioned SQLite storage for runtime sections and managed jobs."""

    def __init__(self, path: Path, legacy_state_path: Path | None = None):
        self.path = Path(path)
        self.legacy_state_path = Path(legacy_state_path) if legacy_state_path else None
        self._init_lock = threading.Lock()
        self._initialized = False

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self._ensure_initialized()
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
        finally:
            connection.close()
            self._secure_runtime_files()

    def _raw_connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        return connection

    def _secure_runtime_files(self) -> None:
        for path in (self.path, Path(str(self.path) + "-wal"), Path(str(self.path) + "-shm")):
            if not path.exists():
                continue
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            connection = self._raw_connect()
            try:
                self._apply_migrations(connection)
                self._import_legacy_state(connection)
            finally:
                connection.close()
                self._secure_runtime_files()
            self._initialized = True

    @staticmethod
    def _apply_migrations(connection: sqlite3.Connection) -> None:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        connection.commit()
        migrations = {
            1: (
                "CREATE TABLE IF NOT EXISTS runtime_sections ("
                "section_key TEXT PRIMARY KEY, payload_json TEXT NOT NULL, updated_at TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS runtime_metadata ("
                "metadata_key TEXT PRIMARY KEY, metadata_value TEXT NOT NULL, updated_at TEXT NOT NULL)",
            ),
            2: (
                "CREATE TABLE IF NOT EXISTS managed_jobs ("
                "job_id TEXT PRIMARY KEY, status TEXT NOT NULL, kind TEXT NOT NULL, "
                "pid INTEGER, process_start_ticks INTEGER, boot_id TEXT, command_sha256 TEXT, "
                "payload_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
                "CREATE INDEX IF NOT EXISTS managed_jobs_status_idx ON managed_jobs(status, updated_at)",
            ),
        }
        connection.execute("BEGIN IMMEDIATE")
        try:
            applied = {
                int(row["version"])
                for row in connection.execute("SELECT version FROM schema_migrations")
            }
            newer = sorted(version for version in applied if version > RUNTIME_SCHEMA_VERSION)
            if newer:
                raise RuntimeError(
                    f"runtime schema {newer[-1]} is newer than supported schema {RUNTIME_SCHEMA_VERSION}"
                )
            for version in range(1, RUNTIME_SCHEMA_VERSION + 1):
                if version in applied:
                    continue
                for statement in migrations[version]:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (version, _utc_now()),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def _import_legacy_state(self, connection: sqlite3.Connection) -> None:
        existing = connection.execute("SELECT 1 FROM runtime_sections LIMIT 1").fetchone()
        if existing or self.legacy_state_path is None or not self.legacy_state_path.exists():
            return
        try:
            payload = json.loads(self.legacy_state_path.read_text())
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return
        now = _utc_now()
        with connection:
            for key, value in payload.items():
                connection.execute(
                    "INSERT INTO runtime_sections(section_key, payload_json, updated_at) VALUES (?, ?, ?)",
                    (str(key), json.dumps(value, sort_keys=True, separators=(",", ":")), now),
                )
            connection.execute(
                "INSERT OR REPLACE INTO runtime_metadata(metadata_key, metadata_value, updated_at) "
                "VALUES ('legacy_json_import', ?, ?)",
                (str(self.legacy_state_path), now),
            )

    def read_state(self, default_state: dict) -> dict:
        state = json.loads(json.dumps(default_state))
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT section_key, payload_json FROM runtime_sections"
            ).fetchall()
        for row in rows:
            try:
                state[str(row["section_key"])] = json.loads(row["payload_json"])
            except json.JSONDecodeError:
                continue
        return state

    def write_state(self, state: dict) -> None:
        now = _utc_now()
        encoded = {
            str(key): json.dumps(value, sort_keys=True, separators=(",", ":"))
            for key, value in state.items()
        }
        with self.connect() as connection, connection:
            existing = {
                str(row["section_key"]): str(row["payload_json"])
                for row in connection.execute("SELECT section_key, payload_json FROM runtime_sections")
            }
            for key, payload_json in encoded.items():
                if existing.get(key) == payload_json:
                    continue
                connection.execute(
                    "INSERT INTO runtime_sections(section_key, payload_json, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(section_key) DO UPDATE SET payload_json=excluded.payload_json, updated_at=excluded.updated_at",
                    (key, payload_json, now),
                )
            removed = set(existing) - set(encoded)
            if removed:
                connection.executemany(
                    "DELETE FROM runtime_sections WHERE section_key = ?",
                    [(key,) for key in removed],
                )

    def schema_version(self) -> int:
        with self.connect() as connection:
            row = connection.execute("SELECT MAX(version) AS version FROM schema_migrations").fetchone()
        return int(row["version"] or 0) if row else 0

    def upsert_job(self, job: dict, identity: dict | None = None) -> None:
        identity = identity or {}
        now = _utc_now()
        job_id = str(job["id"])
        created = str(job.get("created_at") or job.get("start_ts") or now)
        payload_json = json.dumps(job, sort_keys=True, separators=(",", ":"))
        with self.connect() as connection, connection:
            connection.execute(
                "INSERT INTO managed_jobs("
                "job_id, status, kind, pid, process_start_ticks, boot_id, command_sha256, "
                "payload_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(job_id) DO UPDATE SET "
                "status=excluded.status, kind=excluded.kind, pid=excluded.pid, "
                "process_start_ticks=excluded.process_start_ticks, boot_id=excluded.boot_id, "
                "command_sha256=excluded.command_sha256, payload_json=excluded.payload_json, "
                "updated_at=excluded.updated_at",
                (
                    job_id,
                    str(job.get("status", "unknown")),
                    str(job.get("kind", "unknown")),
                    int(job["pid"]) if job.get("pid") is not None else None,
                    identity.get("process_start_ticks"),
                    identity.get("boot_id"),
                    identity.get("command_sha256"),
                    payload_json,
                    created,
                    now,
                ),
            )

    def get_job(self, job_id: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM managed_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return self._decode_job_row(row) if row else None

    def list_jobs(self) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM managed_jobs ORDER BY created_at DESC"
            ).fetchall()
        return [self._decode_job_row(row) for row in rows]

    @staticmethod
    def _decode_job_row(row: sqlite3.Row) -> dict:
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            payload = {"id": row["job_id"], "kind": row["kind"], "status": row["status"]}
        payload["_process_identity"] = {
            "process_start_ticks": row["process_start_ticks"],
            "boot_id": row["boot_id"],
            "command_sha256": row["command_sha256"],
        }
        return payload
