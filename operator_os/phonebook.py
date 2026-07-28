"""Phonebook contacts — inbound name resolve + outside-line short codes."""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from operator_os import db as store
from operator_os.sip import normalize_nanp, speak_phone_number


@dataclass(frozen=True)
class Contact:
    id: int
    name: str
    e164: str
    short_code: str
    notes: str
    created_at: float


_SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    e164 TEXT NOT NULL UNIQUE,
    short_code TEXT,
    notes TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_short
    ON contacts (short_code) WHERE short_code IS NOT NULL AND short_code != '';
CREATE INDEX IF NOT EXISTS idx_contacts_e164 ON contacts (e164);
"""


def ensure_schema() -> None:
    store.init_db()
    conn = store._connection()
    with store._lock:
        conn.executescript(_SCHEMA)
        conn.commit()


def _row(row: sqlite3.Row) -> Contact:
    return Contact(
        id=int(row["id"]),
        name=str(row["name"] or ""),
        e164=str(row["e164"] or ""),
        short_code=str(row["short_code"] or ""),
        notes=str(row["notes"] or ""),
        created_at=float(row["created_at"]),
    )


def _norm_e164(raw: str) -> str | None:
    return normalize_nanp(raw)


def _norm_short(raw: str) -> str:
    return re.sub(r"\D", "", raw or "")


def list_contacts(*, limit: int = 200) -> list[Contact]:
    ensure_schema()
    conn = store._connection()
    with store._lock:
        rows = conn.execute(
            """
            SELECT * FROM contacts
            ORDER BY name COLLATE NOCASE ASC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
    return [_row(r) for r in rows]


def get_contact(contact_id: int) -> Contact | None:
    ensure_schema()
    conn = store._connection()
    with store._lock:
        row = conn.execute(
            "SELECT * FROM contacts WHERE id = ?", (int(contact_id),)
        ).fetchone()
    return _row(row) if row else None


def lookup_by_e164(e164: str) -> Contact | None:
    ensure_schema()
    norm = _norm_e164(e164) or (e164 or "").strip()
    if not norm:
        return None
    conn = store._connection()
    with store._lock:
        row = conn.execute(
            "SELECT * FROM contacts WHERE e164 = ?", (norm,)
        ).fetchone()
        if row is None and norm.startswith("+"):
            # Also try raw digits match variants stored without +
            row = conn.execute(
                "SELECT * FROM contacts WHERE replace(e164, '+', '') = ?",
                (re.sub(r"\D", "", norm),),
            ).fetchone()
    return _row(row) if row else None


def lookup_by_short_code(code: str) -> Contact | None:
    ensure_schema()
    sc = _norm_short(code)
    if not sc:
        return None
    conn = store._connection()
    with store._lock:
        row = conn.execute(
            "SELECT * FROM contacts WHERE short_code = ?", (sc,)
        ).fetchone()
    return _row(row) if row else None


def lookup_by_name(name: str) -> Contact | None:
    """Exact case-insensitive name match (console dial-by-name)."""
    ensure_schema()
    n = (name or "").strip()
    if not n:
        return None
    conn = store._connection()
    with store._lock:
        row = conn.execute(
            "SELECT * FROM contacts WHERE name = ? COLLATE NOCASE",
            (n,),
        ).fetchone()
    return _row(row) if row else None


def display_name(e164: str) -> str | None:
    c = lookup_by_e164(e164)
    return c.name if c and c.name else None


def speak_from(e164: str) -> str:
    """Name if known, else spoken digit groups."""
    name = display_name(e164)
    if name:
        return name
    if not (e164 or "").strip():
        return "an unknown caller"
    return speak_phone_number(e164)


def resolve_outside_digits(raw: str) -> str | None:
    """Short code first, then NANP. Chart place_call path unchanged."""
    digits = _norm_short(raw)
    if not digits:
        return None
    hit = lookup_by_short_code(digits)
    if hit is not None:
        return hit.e164
    return normalize_nanp(raw)


def upsert_contact(
    *,
    name: str,
    e164: str,
    short_code: str = "",
    notes: str = "",
    contact_id: int | None = None,
) -> Contact:
    ensure_schema()
    nm = (name or "").strip()
    if not nm:
        raise ValueError("name required")
    dest = _norm_e164(e164)
    if not dest:
        raise ValueError("invalid e164")
    sc = _norm_short(short_code) or None
    notes = (notes or "").strip()
    conn = store._connection()
    with store._lock:
        if contact_id is None:
            existing = conn.execute(
                "SELECT id FROM contacts WHERE e164 = ?", (dest,)
            ).fetchone()
            if existing is not None:
                contact_id = int(existing["id"])
        if contact_id is not None:
            try:
                conn.execute(
                    """
                    UPDATE contacts
                    SET name = ?, e164 = ?, short_code = ?, notes = ?
                    WHERE id = ?
                    """,
                    (nm, dest, sc, notes, int(contact_id)),
                )
                conn.commit()
            except sqlite3.IntegrityError as e:
                raise ValueError("duplicate e164 or short_code") from e
            row = conn.execute(
                "SELECT * FROM contacts WHERE id = ?", (int(contact_id),)
            ).fetchone()
            if row is None:
                raise ValueError("not found")
            return _row(row)
        now = time.time()
        try:
            cur = conn.execute(
                """
                INSERT INTO contacts (name, e164, short_code, notes, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (nm, dest, sc, notes, now),
            )
            conn.commit()
        except sqlite3.IntegrityError as e:
            raise ValueError("duplicate e164 or short_code") from e
        row = conn.execute(
            "SELECT * FROM contacts WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
    assert row is not None
    return _row(row)


def delete_contact(contact_id: int) -> bool:
    ensure_schema()
    conn = store._connection()
    with store._lock:
        cur = conn.execute("DELETE FROM contacts WHERE id = ?", (int(contact_id),))
        conn.commit()
        return cur.rowcount > 0


def contact_to_dict(c: Contact) -> dict[str, Any]:
    return {
        "id": c.id,
        "name": c.name,
        "e164": c.e164,
        "short_code": c.short_code,
        "notes": c.notes,
        "created_at": c.created_at,
    }
