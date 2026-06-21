"""SQLite persistence for Bill-of-Lading records (replaces the in-memory dict).

Survives server restarts, so a real escrow link keeps working after the process
recycles. Thread-safe for the uvicorn worker pool via a single guarded connection.
"""

from __future__ import annotations

import datetime
import json
import os
import sqlite3
import threading

_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "aerofreight.db")
_lock = threading.Lock()


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


_db = _connect()
with _db:
    _db.execute(
        """
        CREATE TABLE IF NOT EXISTS bols (
            contract_id TEXT PRIMARY KEY,
            payload     TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'escrow_pending',
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )
        """
    )


def save_bol(record: dict) -> dict:
    """Insert or update a BoL record (keyed by contract_id)."""
    cid = record["contract_id"]
    now = _now()
    with _lock, _db:
        existing = _db.execute(
            "SELECT created_at FROM bols WHERE contract_id = ?", (cid,)
        ).fetchone()
        created = existing["created_at"] if existing else now
        _db.execute(
            """
            INSERT INTO bols (contract_id, payload, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(contract_id) DO UPDATE SET
                payload = excluded.payload,
                status = excluded.status,
                updated_at = excluded.updated_at
            """,
            (cid, json.dumps(record), record.get("status", "escrow_pending"), created, now),
        )
    return record


def get_bol(contract_id: str) -> dict | None:
    with _lock:
        row = _db.execute(
            "SELECT payload FROM bols WHERE contract_id = ?", (contract_id,)
        ).fetchone()
    return json.loads(row["payload"]) if row else None


def update_bol(contract_id: str, **fields) -> dict | None:
    """Merge ``fields`` into a stored record and persist it."""
    record = get_bol(contract_id)
    if record is None:
        return None
    record.update(fields)
    save_bol(record)
    return record
