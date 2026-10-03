"""Durable job store (SQLite).

Guarantees:

* **Idempotency** - submitting the same ``idempotency_key`` twice returns the
  SAME job; a duplicate expensive run is never created.
* **Crash safety** - status, attempt count, checkpoint and result are
  persisted per transition; a worker crash never loses a finished run and a
  resumable run can be continued from its last checkpoint.
* **Cancellation** - ``cancel`` is a durable flag the running loop polls.
* **Leasing** - ``lease`` atomically moves a job PENDING -> RUNNING so two
  workers cannot double-execute it.

All timestamps are UTC ISO-8601.  Serialized payloads are secret-redacted
before they hit the database.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from typing import Any, Dict, List, Optional

from odar.trust import redact_secrets

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id          TEXT PRIMARY KEY,
    idempotency_key TEXT UNIQUE,
    objective       TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'PENDING',
    attempt         INTEGER NOT NULL DEFAULT 0,
    node_id         TEXT,
    checkpoint      TEXT NOT NULL DEFAULT '{}',
    result          TEXT,
    error           TEXT,
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    finished_at     TEXT,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE TABLE IF NOT EXISTS job_events (
    job_id   TEXT NOT NULL,
    ts       TEXT NOT NULL,
    name     TEXT NOT NULL,
    payload  TEXT NOT NULL,
    FOREIGN KEY(job_id) REFERENCES jobs(job_id)
);
"""

TERMINAL_STATUSES = ("COMPLETE", "INCOMPLETE", "BUDGET_EXHAUSTED", "CANCELLED", "FAILED", "SECURITY_HALT")


def _utcnow() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def job_id_for_key(idempotency_key: str) -> str:
    """Deterministic job id derived from the idempotency key."""
    return "job_" + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:24]


class JobStore:
    def __init__(self, path: str = "odar_jobs.db") -> None:
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ------------------------------------------------------------------ #
    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ #
    def create_job(self, objective: str, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        """Create a job, or return the existing one for this idempotency key."""
        now = _utcnow()
        with self._lock:
            if idempotency_key:
                row = self._conn.execute(
                    "SELECT * FROM jobs WHERE idempotency_key = ?", (idempotency_key,)
                ).fetchone()
                if row is not None:
                    return dict(row)
                job_id = job_id_for_key(idempotency_key)
            else:
                job_id = "job_" + uuid.uuid4().hex[:24]
            self._conn.execute(
                "INSERT INTO jobs (job_id, idempotency_key, objective, status, attempt, "
                "checkpoint, created_at, updated_at) VALUES (?,?,?,?,0,'{}',?,?)",
                (job_id, idempotency_key, redact_secrets(objective), "PENDING", now, now),
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            return dict(row)

    # ------------------------------------------------------------------ #
    def lease(self, job_id: str, node_id: str) -> bool:
        """Atomically claim a PENDING job.  Returns False if someone else has it."""
        now = _utcnow()
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE jobs SET status='RUNNING', node_id=?, attempt=attempt+1, "
                "started_at=COALESCE(started_at, ?), updated_at=? "
                "WHERE job_id=? AND status IN ('PENDING','RESUME')",
                (node_id, now, now, job_id),
            )
            self._conn.commit()
            return cursor.rowcount == 1

    #: statuses from which a resume is meaningful (COMPLETE is never requeued)
    RESUMABLE_STATUSES = ("RUNNING", "BUDGET_EXHAUSTED", "CANCELLED", "FAILED", "INCOMPLETE")

    def requeue(self, job_id: str) -> None:
        placeholders = ",".join("?" for _ in self.RESUMABLE_STATUSES)
        with self._lock:
            self._conn.execute(
                f"UPDATE jobs SET status='RESUME', updated_at=? "
                f"WHERE job_id=? AND status IN ({placeholders})",
                (_utcnow(), job_id, *self.RESUMABLE_STATUSES),
            )
            self._conn.commit()

    # ------------------------------------------------------------------ #
    def set_status(
        self, job_id: str, status: str, error: Optional[str] = None, result: Optional[str] = None
    ) -> None:
        now = _utcnow()
        finished = now if status in TERMINAL_STATUSES else None
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET status=?, error=?, result=?, finished_at=COALESCE(?, finished_at), "
                "updated_at=? WHERE job_id=?",
                (status, redact_secrets(error or ""), redact_secrets(result or ""), finished, now, job_id),
            )
            self._conn.commit()

    def save_checkpoint(self, job_id: str, checkpoint: Dict[str, Any]) -> None:
        blob = redact_secrets(json.dumps(checkpoint, ensure_ascii=False, default=str))
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET checkpoint=?, updated_at=? WHERE job_id=?",
                (blob, _utcnow(), job_id),
            )
            self._conn.commit()

    def append_event(self, job_id: str, name: str, payload: Dict[str, Any]) -> None:
        blob = redact_secrets(json.dumps(payload, ensure_ascii=False, default=str))
        with self._lock:
            self._conn.execute(
                "INSERT INTO job_events (job_id, ts, name, payload) VALUES (?,?,?,?)",
                (job_id, _utcnow(), name, blob),
            )
            self._conn.commit()

    # ------------------------------------------------------------------ #
    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            return dict(row) if row else None

    def checkpoint(self, job_id: str) -> Dict[str, Any]:
        job = self.get(job_id)
        if not job or not job.get("checkpoint"):
            return {}
        try:
            return json.loads(job["checkpoint"])
        except json.JSONDecodeError:
            return {}

    def events(self, job_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, name, payload FROM job_events WHERE job_id=? ORDER BY rowid DESC LIMIT ?",
                (job_id, limit),
            ).fetchall()
        events = []
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except json.JSONDecodeError:
                payload = {}
            events.append({"ts": row["ts"], "name": row["name"], "payload": payload})
        return list(reversed(events))

    def list_jobs(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT job_id, idempotency_key, objective, status, attempt, created_at, "
                "started_at, finished_at, updated_at FROM jobs ORDER BY rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    # ------------------------------------------------------------------ #
    def cancel(self, job_id: str) -> bool:
        """Request cancellation.  Terminal jobs cannot be cancelled."""
        with self._lock:
            row = self._conn.execute("SELECT status FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None or row["status"] in TERMINAL_STATUSES:
                return False
            self._conn.execute(
                "UPDATE jobs SET status='CANCEL_REQUESTED', updated_at=? WHERE job_id=?",
                (_utcnow(), job_id),
            )
            self._conn.commit()
            return True

    def is_cancel_requested(self, job_id: str) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT status FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            return bool(row and row["status"] == "CANCEL_REQUESTED")

    def finalize_cancel(self, job_id: str) -> None:
        self.set_status(job_id, "CANCELLED", error="cancelled by user request")
