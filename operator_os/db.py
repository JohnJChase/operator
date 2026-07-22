"""Stdlib SQLite store for messages and voicemail (Block 4+)."""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "operator.sqlite3"

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None
_db_path: Path = DEFAULT_DB

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telnyx_id TEXT UNIQUE,
    direction TEXT NOT NULL CHECK (direction IN ('in', 'out')),
    from_e164 TEXT NOT NULL DEFAULT '',
    to_e164 TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    heard_at REAL,
    status TEXT NOT NULL DEFAULT 'queued'
);
CREATE INDEX IF NOT EXISTS idx_messages_unheard
    ON messages (direction, heard_at, created_at);

CREATE TABLE IF NOT EXISTS voicemails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_e164 TEXT NOT NULL DEFAULT '',
    path TEXT NOT NULL,
    created_at REAL NOT NULL,
    heard_at REAL,
    duration_s REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'new'
);
CREATE INDEX IF NOT EXISTS idx_voicemails_unheard
    ON voicemails (heard_at, created_at);
"""


@dataclass(frozen=True)
class Message:
    id: int
    telnyx_id: str | None
    direction: str
    from_e164: str
    to_e164: str
    body: str
    created_at: float
    heard_at: float | None
    status: str


def configure(path: Path | None = None) -> None:
    """Point at a DB file (tests use a temp path). Resets the shared connection."""
    global _conn, _db_path
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None
        _db_path = path if path is not None else DEFAULT_DB


def init_db(path: Path | None = None) -> Path:
    if path is not None:
        configure(path)
    db = _db_path
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = _connection()
    conn.executescript(_SCHEMA)
    conn.commit()
    return db


def _connection() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            _db_path.parent.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(str(_db_path), check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
        return _conn


def _row_to_message(row: sqlite3.Row) -> Message:
    return Message(
        id=int(row["id"]),
        telnyx_id=row["telnyx_id"],
        direction=str(row["direction"]),
        from_e164=str(row["from_e164"] or ""),
        to_e164=str(row["to_e164"] or ""),
        body=str(row["body"] or ""),
        created_at=float(row["created_at"]),
        heard_at=float(row["heard_at"]) if row["heard_at"] is not None else None,
        status=str(row["status"] or ""),
    )


def upsert_inbound(
    *,
    telnyx_id: str,
    from_e164: str,
    to_e164: str,
    body: str,
) -> tuple[Message, bool]:
    """Insert inbound SMS. Returns (message, created). Idempotent on telnyx_id."""
    init_db()
    tid = (telnyx_id or "").strip()
    if not tid:
        raise ValueError("telnyx_id required")
    now = time.time()
    conn = _connection()
    with _lock:
        existing = conn.execute(
            "SELECT * FROM messages WHERE telnyx_id = ?", (tid,)
        ).fetchone()
        if existing is not None:
            return _row_to_message(existing), False
        cur = conn.execute(
            """
            INSERT INTO messages (
                telnyx_id, direction, from_e164, to_e164, body,
                created_at, heard_at, status
            ) VALUES (?, 'in', ?, ?, ?, ?, NULL, 'queued')
            """,
            (tid, from_e164.strip(), to_e164.strip(), body, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM messages WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
    assert row is not None
    return _row_to_message(row), True


def insert_outbound(
    *,
    to_e164: str,
    from_e164: str,
    body: str,
    telnyx_id: str | None = None,
    status: str = "sent",
) -> Message:
    init_db()
    now = time.time()
    conn = _connection()
    with _lock:
        cur = conn.execute(
            """
            INSERT INTO messages (
                telnyx_id, direction, from_e164, to_e164, body,
                created_at, heard_at, status
            ) VALUES (?, 'out', ?, ?, ?, ?, ?, ?)
            """,
            (
                (telnyx_id or "").strip() or None,
                from_e164.strip(),
                to_e164.strip(),
                body,
                now,
                now,
                status,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM messages WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
    assert row is not None
    return _row_to_message(row)


def list_unheard(*, limit: int = 20) -> list[Message]:
    init_db()
    conn = _connection()
    with _lock:
        rows = conn.execute(
            """
            SELECT * FROM messages
            WHERE direction = 'in' AND heard_at IS NULL
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
    return [_row_to_message(r) for r in rows]


def unheard_count() -> int:
    init_db()
    conn = _connection()
    with _lock:
        row = conn.execute(
            """
            SELECT COUNT(*) AS n FROM messages
            WHERE direction = 'in' AND heard_at IS NULL
            """
        ).fetchone()
    return int(row["n"] if row else 0)


def mark_heard(message_id: int) -> Message | None:
    init_db()
    now = time.time()
    conn = _connection()
    with _lock:
        conn.execute(
            """
            UPDATE messages
            SET heard_at = ?, status = 'heard'
            WHERE id = ? AND heard_at IS NULL
            """,
            (now, int(message_id)),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM messages WHERE id = ?", (int(message_id),)
        ).fetchone()
    return _row_to_message(row) if row else None


def get_message(message_id: int) -> Message | None:
    init_db()
    conn = _connection()
    with _lock:
        row = conn.execute(
            "SELECT * FROM messages WHERE id = ?", (int(message_id),)
        ).fetchone()
    return _row_to_message(row) if row else None


@dataclass(frozen=True)
class Voicemail:
    id: int
    from_e164: str
    path: str
    created_at: float
    heard_at: float | None
    duration_s: float
    status: str


def _row_to_voicemail(row: sqlite3.Row) -> Voicemail:
    return Voicemail(
        id=int(row["id"]),
        from_e164=str(row["from_e164"] or ""),
        path=str(row["path"] or ""),
        created_at=float(row["created_at"]),
        heard_at=float(row["heard_at"]) if row["heard_at"] is not None else None,
        duration_s=float(row["duration_s"] or 0),
        status=str(row["status"] or ""),
    )


def insert_voicemail(
    *,
    from_e164: str,
    path: str,
    duration_s: float = 0.0,
) -> Voicemail:
    init_db()
    now = time.time()
    conn = _connection()
    with _lock:
        cur = conn.execute(
            """
            INSERT INTO voicemails (from_e164, path, created_at, heard_at, duration_s, status)
            VALUES (?, ?, ?, NULL, ?, 'new')
            """,
            (from_e164.strip(), str(path), now, float(duration_s)),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM voicemails WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
    assert row is not None
    return _row_to_voicemail(row)


def list_unheard_voicemails(*, limit: int = 20) -> list[Voicemail]:
    init_db()
    conn = _connection()
    with _lock:
        rows = conn.execute(
            """
            SELECT * FROM voicemails
            WHERE heard_at IS NULL
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
    return [_row_to_voicemail(r) for r in rows]


def unheard_voicemail_count() -> int:
    init_db()
    conn = _connection()
    with _lock:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM voicemails WHERE heard_at IS NULL"
        ).fetchone()
    return int(row["n"] if row else 0)


def get_voicemail(voicemail_id: int) -> Voicemail | None:
    init_db()
    conn = _connection()
    with _lock:
        row = conn.execute(
            "SELECT * FROM voicemails WHERE id = ?", (int(voicemail_id),)
        ).fetchone()
    return _row_to_voicemail(row) if row else None


def mark_voicemail_heard(voicemail_id: int) -> Voicemail | None:
    init_db()
    now = time.time()
    conn = _connection()
    with _lock:
        conn.execute(
            """
            UPDATE voicemails
            SET heard_at = ?, status = 'heard'
            WHERE id = ? AND heard_at IS NULL
            """,
            (now, int(voicemail_id)),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM voicemails WHERE id = ?", (int(voicemail_id),)
        ).fetchone()
    return _row_to_voicemail(row) if row else None


def delete_voicemail(voicemail_id: int) -> bool:
    init_db()
    conn = _connection()
    with _lock:
        row = conn.execute(
            "SELECT path FROM voicemails WHERE id = ?", (int(voicemail_id),)
        ).fetchone()
        if row is None:
            return False
        path = Path(str(row["path"]))
        conn.execute("DELETE FROM voicemails WHERE id = ?", (int(voicemail_id),))
        conn.commit()
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    return True


@dataclass(frozen=True)
class WaitingItem:
    """One unheard SMS or voicemail for the universal inbox (chrono merge)."""

    kind: str  # "sms" | "vm"
    id: int
    from_e164: str
    created_at: float
    body: str = ""
    path: str = ""


def waiting_count() -> int:
    return unheard_count() + unheard_voicemail_count()


def list_waiting_chrono(*, limit: int = 20) -> list[WaitingItem]:
    """Unheard inbound SMS + voicemails, oldest first."""
    init_db()
    lim = max(1, int(limit))
    items: list[WaitingItem] = []
    for m in list_unheard(limit=lim):
        items.append(
            WaitingItem(
                kind="sms",
                id=m.id,
                from_e164=m.from_e164,
                created_at=m.created_at,
                body=m.body,
            )
        )
    for vm in list_unheard_voicemails(limit=lim):
        items.append(
            WaitingItem(
                kind="vm",
                id=vm.id,
                from_e164=vm.from_e164,
                created_at=vm.created_at,
                path=vm.path,
            )
        )
    items.sort(key=lambda x: x.created_at)
    return items[:lim]
