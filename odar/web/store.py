"""Run history for the web app (SQLite, one row per Research/Check run).

Privacy: uploaded files are never written here. A run keeps only the text it
needs to display its report (the question, or the checked answer), and that
input can be dropped with ``keep_input=False``.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    share_token TEXT UNIQUE NOT NULL,
    owner TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL,
    title TEXT NOT NULL,
    input_text TEXT NOT NULL DEFAULT '',
    input_meta TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    stage TEXT NOT NULL DEFAULT '',
    error TEXT,
    result TEXT,
    created REAL NOT NULL,
    updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS run_events (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    t REAL NOT NULL,
    kind TEXT NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);
CREATE INDEX IF NOT EXISTS runs_owner_created ON runs(owner, created DESC);
CREATE INDEX IF NOT EXISTS runs_created ON runs(created);
CREATE TABLE IF NOT EXISTS api_keys (
    key_hash TEXT PRIMARY KEY,
    prefix TEXT UNIQUE NOT NULL,
    owner TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    daily_limit INTEGER NOT NULL,
    created REAL NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS usage (
    subject TEXT NOT NULL,
    bucket TEXT NOT NULL,
    count INTEGER NOT NULL,
    expires REAL NOT NULL,
    PRIMARY KEY (subject, bucket)
);
CREATE TABLE IF NOT EXISTS http_cache (
    key TEXT PRIMARY KEY,
    status INTEGER NOT NULL,
    body TEXT NOT NULL,
    expires REAL NOT NULL
);
"""

ACTIVE = ("QUEUED", "RUNNING")


class RunStore:
    def __init__(self, path: str = "odar_web.db") -> None:
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ #
    def create(
        self,
        mode: str,
        title: str,
        input_text: str,
        input_meta: Optional[Dict[str, Any]] = None,
        owner: str = "",
        keep_input: bool = True,
    ) -> Dict[str, Any]:
        run_id = secrets.token_hex(8)
        token = secrets.token_urlsafe(12)
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO runs (run_id, share_token, owner, mode, title, input_text, input_meta, "
                "status, stage, created, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    token,
                    owner,
                    mode,
                    title[:200],
                    input_text if keep_input else "",
                    json.dumps(input_meta or {}),
                    "QUEUED",
                    "queued",
                    now,
                    now,
                ),
            )
            self._conn.commit()
        run = self.get(run_id)
        assert run is not None
        return run

    def update(self, run_id: str, **fields: Any) -> None:
        allowed = {"status", "stage", "error", "result", "title"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if "result" in sets and not isinstance(sets["result"], (str, type(None))):
            sets["result"] = json.dumps(sets["result"], ensure_ascii=False, default=str)
        sets["updated"] = time.time()
        cols = ", ".join(f"{k} = ?" for k in sets)
        with self._lock:
            self._conn.execute(f"UPDATE runs SET {cols} WHERE run_id = ?", (*sets.values(), run_id))
            self._conn.commit()

    def add_event(self, run_id: str, kind: str, data: Optional[Dict[str, Any]] = None) -> None:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM run_events WHERE run_id = ?", (run_id,)
            ).fetchone()
            self._conn.execute(
                "INSERT INTO run_events (run_id, seq, t, kind, data) VALUES (?,?,?,?,?)",
                (run_id, int(row[0]) + 1, time.time(), kind, json.dumps(data or {}, default=str)[:4000]),
            )
            self._conn.commit()

    def events(self, run_id: str, after: int = 0, limit: int = 500) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT seq, t, kind, data FROM run_events WHERE run_id = ? AND seq > ? ORDER BY seq LIMIT ?",
                (run_id, after, limit),
            ).fetchall()
        return [
            {"seq": r["seq"], "t": r["t"], "kind": r["kind"], "data": json.loads(r["data"])} for r in rows
        ]

    # ------------------------------------------------------------------ #
    def _row(self, row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        data = dict(row)
        data["input_meta"] = json.loads(data.get("input_meta") or "{}")
        data["result"] = json.loads(data["result"]) if data.get("result") else None
        return data

    def get(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return self._row(row)

    def by_token(self, token: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM runs WHERE share_token = ?", (token,)).fetchone()
        return self._row(row)

    def history(self, owner: str = "", limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT run_id, share_token, mode, title, status, stage, created, updated FROM runs "
                "WHERE owner = ? ORDER BY created DESC LIMIT ?",
                (owner, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete(self, run_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
            self._conn.execute("DELETE FROM run_events WHERE run_id = ?", (run_id,))
            self._conn.commit()
        return cur.rowcount > 0

    def requeue_stale(self) -> int:
        """Runs left RUNNING by a crashed server are marked failed on start."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE runs SET status = 'FAILED', error = 'server restarted mid-run', updated = ? "
                "WHERE status IN ('QUEUED', 'RUNNING')",
                (time.time(),),
            )
            self._conn.commit()
        return cur.rowcount

    # ------------------------------------------------------------------ #
    # Privacy: retention + delete-everything
    # ------------------------------------------------------------------ #
    def purge_older_than(self, days: float) -> int:
        if days <= 0:
            return 0
        cutoff = time.time() - days * 86400
        with self._lock:
            ids = [r[0] for r in self._conn.execute("SELECT run_id FROM runs WHERE created < ?", (cutoff,))]
            self._conn.executemany("DELETE FROM run_events WHERE run_id = ?", [(i,) for i in ids])
            self._conn.execute("DELETE FROM runs WHERE created < ?", (cutoff,))
            self._conn.execute("DELETE FROM usage WHERE expires < ?", (time.time(),))
            self._conn.execute("DELETE FROM http_cache WHERE expires < ?", (time.time(),))
            self._conn.commit()
        return len(ids)

    def delete_owner(self, owner: str) -> int:
        if not owner:
            return 0
        with self._lock:
            ids = [r[0] for r in self._conn.execute("SELECT run_id FROM runs WHERE owner = ?", (owner,))]
            self._conn.executemany("DELETE FROM run_events WHERE run_id = ?", [(i,) for i in ids])
            self._conn.execute("DELETE FROM runs WHERE owner = ?", (owner,))
            self._conn.execute("UPDATE api_keys SET revoked = 1 WHERE owner = ?", (owner,))
            self._conn.commit()
        return len(ids)

    # ------------------------------------------------------------------ #
    # Rate limits (fixed windows)
    # ------------------------------------------------------------------ #
    def hit(self, subject: str, window_s: int, limit: int, cost: int = 1) -> bool:
        """Count one use in the current window; False when over the limit (not counted)."""
        now = time.time()
        bucket = str(int(now // window_s))
        with self._lock:
            row = self._conn.execute(
                "SELECT count FROM usage WHERE subject = ? AND bucket = ?", (f"{subject}@{window_s}", bucket)
            ).fetchone()
            used = int(row[0]) if row else 0
            if used + cost > limit:
                return False
            self._conn.execute(
                "INSERT INTO usage (subject, bucket, count, expires) VALUES (?,?,?,?) "
                "ON CONFLICT(subject, bucket) DO UPDATE SET count = count + ?",
                (f"{subject}@{window_s}", bucket, cost, now + window_s, cost),
            )
            self._conn.commit()
        return True

    def used(self, subject: str, window_s: int) -> int:
        bucket = str(int(time.time() // window_s))
        with self._lock:
            row = self._conn.execute(
                "SELECT count FROM usage WHERE subject = ? AND bucket = ?", (f"{subject}@{window_s}", bucket)
            ).fetchone()
        return int(row[0]) if row else 0

    # ------------------------------------------------------------------ #
    # API keys (only a SHA-256 hash is stored; the key is shown once)
    # ------------------------------------------------------------------ #
    def create_key(self, owner: str, label: str = "", daily_limit: int = 50) -> str:
        key = "odar_" + secrets.token_urlsafe(24)
        with self._lock:
            self._conn.execute(
                "INSERT INTO api_keys (key_hash, prefix, owner, label, daily_limit, created) VALUES (?,?,?,?,?,?)",
                (_hash(key), key[:12], owner, label[:60], int(daily_limit), time.time()),
            )
            self._conn.commit()
        return key

    def key_owner(self, key: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT prefix, owner, label, daily_limit FROM api_keys WHERE key_hash = ? AND revoked = 0",
                (_hash(key),),
            ).fetchone()
        return dict(row) if row else None

    def list_keys(self, owner: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT prefix, label, daily_limit, created FROM api_keys WHERE owner = ? AND revoked = 0 "
                "ORDER BY created DESC",
                (owner,),
            ).fetchall()
        keys = [dict(r) for r in rows]
        for k in keys:
            k["used_today"] = self.used(f"key:{k['prefix']}", 86400)
        return keys

    def revoke_key(self, owner: str, prefix: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE api_keys SET revoked = 1 WHERE owner = ? AND prefix = ? AND revoked = 0",
                (owner, prefix),
            )
            self._conn.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------------ #
    # HTTP cache for scholarly API responses (Tier 4 caching)
    # ------------------------------------------------------------------ #
    def cache_get(self, key: str) -> Optional[tuple]:
        with self._lock:
            row = self._conn.execute(
                "SELECT status, body FROM http_cache WHERE key = ? AND expires > ?", (key, time.time())
            ).fetchone()
        return (int(row[0]), row[1]) if row else None

    def cache_put(self, key: str, status: int, body: str, ttl_s: float) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO http_cache (key, status, body, expires) VALUES (?,?,?,?)",
                (key, status, body[:2_000_000], time.time() + ttl_s),
            )
            self._conn.commit()


def _hash(key: str) -> str:
    import hashlib

    return hashlib.sha256(key.encode()).hexdigest()
