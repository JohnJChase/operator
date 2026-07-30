"""Ordered station lists for failover intents (Meet, open URL)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PRIORITY_PATH = ROOT / "data" / "route_priority.json"

WE302_MEET_ID = "we302-meet"

# Product keys (not wire desktop.* names).
INTENT_OPEN_MEETING = "open.meeting"
INTENT_OPEN_URL = "open.url"
INTENT_NOTIFY = "notify.messages"

EDITABLE_INTENTS = (INTENT_OPEN_MEETING, INTENT_OPEN_URL)

_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def priority_path() -> Path:
    return PRIORITY_PATH


def clean_station_id(raw: str) -> str:
    s = _ID_RE.sub("-", str(raw or "").strip())
    return s.strip("-._")[:64]


def default_priorities() -> dict[str, list[str]]:
    """Bootstrap from env when the JSON file is missing."""
    meet = _parse_csv(os.environ.get("OPERATOR_ROUTE_OPEN_MEETING", ""))
    if not meet:
        preferred = clean_station_id(os.environ.get("OPERATOR_DESKTOP_CLIENT_ID", ""))
        meet = [x for x in (preferred, WE302_MEET_ID) if x]
        if not meet:
            meet = [WE302_MEET_ID]
    urls = _parse_csv(os.environ.get("OPERATOR_ROUTE_OPEN_URL", ""))
    if not urls:
        preferred = clean_station_id(os.environ.get("OPERATOR_DESKTOP_CLIENT_ID", ""))
        urls = [preferred] if preferred else []
    return {
        INTENT_OPEN_MEETING: meet,
        INTENT_OPEN_URL: urls,
    }


def load_priorities() -> dict[str, list[str]]:
    path = PRIORITY_PATH
    defaults = default_priorities()
    if not path.is_file():
        return {k: list(v) for k, v in defaults.items()}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {k: list(v) for k, v in defaults.items()}
    if not isinstance(raw, dict):
        return {k: list(v) for k, v in defaults.items()}
    out = {k: list(v) for k, v in defaults.items()}
    for key in EDITABLE_INTENTS:
        got = raw.get(key)
        if isinstance(got, list):
            out[key] = _clean_id_list(got)
    return out


def save_priorities(priorities: dict[str, Any]) -> dict[str, list[str]]:
    """Validate and write. Unknown intent keys ignored."""
    current = load_priorities()
    for key in EDITABLE_INTENTS:
        if key not in priorities:
            continue
        got = priorities[key]
        if not isinstance(got, list):
            raise ValueError(f"{key} must be a list of station ids")
        current[key] = _clean_id_list(got)
    path = PRIORITY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(current, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return current


def ensure_station_in_meeting_priority(client_id: str, priorities: dict[str, list[str]]) -> bool:
    """Insert a new open_url station before we302-meet. Returns True if mutated."""
    cid = clean_station_id(client_id)
    if not cid or cid == WE302_MEET_ID:
        return False
    meet = priorities.setdefault(INTENT_OPEN_MEETING, [])
    if cid in meet:
        return False
    if WE302_MEET_ID in meet:
        meet.insert(meet.index(WE302_MEET_ID), cid)
    else:
        meet.append(cid)
    urls = priorities.setdefault(INTENT_OPEN_URL, [])
    if cid not in urls:
        urls.append(cid)
    return True


def _parse_csv(raw: str) -> list[str]:
    return _clean_id_list(part.strip() for part in str(raw or "").split(","))


def _clean_id_list(items) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        cid = clean_station_id(str(item))
        if not cid or cid in seen:
            continue
        seen.add(cid)
        out.append(cid)
    return out
