"""Thread-safe hub between the phone main loop and the diagnostic console."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable


DEFAULT_PORT = 8788
RING_TEST_MS = 1500  # auto-expire; under hardware max_ring_on_ms


def console_password() -> str:
    return os.environ.get("OPERATOR_CONSOLE_PASSWORD", "").strip()


def console_port() -> int:
    raw = os.environ.get("OPERATOR_CONSOLE_PORT", str(DEFAULT_PORT)).strip()
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_PORT


def console_bind() -> str:
    """LAN-reachable bind; override with OPERATOR_CONSOLE_BIND (default 0.0.0.0)."""
    return os.environ.get("OPERATOR_CONSOLE_BIND", "0.0.0.0").strip() or "0.0.0.0"


@dataclass
class ConsoleHub:
    """Main loop writes status; HTTP reads. Ring-test is requested then consumed."""

    events_tail: Callable[[], list[dict[str, Any]]] | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _status: dict[str, Any] = field(default_factory=dict, init=False)
    _sessions: dict[str, float] = field(default_factory=dict, init=False)  # token → expiry
    _ring_request: bool = field(default=False, init=False)
    _place_call: str | None = field(default=None, init=False)
    _digits: deque[int] = field(default_factory=lambda: deque(maxlen=10), init=False)
    _last_digit: int | None = field(default=None, init=False)
    _outside_buffer: str = field(default="", init=False)
    _last_event: str = field(default="", init=False)
    _last_reason: str = field(default="", init=False)
    _session_ttl_s: float = 86400.0

    def note_digit(self, digit: int) -> None:
        with self._lock:
            self._last_digit = digit
            self._digits.append(digit)

    def set_outside_buffer(self, digits: str) -> None:
        with self._lock:
            self._outside_buffer = digits or ""

    def note_transition(self, *, event: str = "", reason: str = "") -> None:
        with self._lock:
            if event:
                self._last_event = event
            if reason:
                self._last_reason = reason

    def publish(self, payload: dict[str, Any]) -> None:
        """Replace live status (called from main loop)."""
        with self._lock:
            extras = {
                "last_digit": self._last_digit,
                "last_digits": list(self._digits),
                "outside_buffer": self._outside_buffer,
                "last_event": self._last_event,
                "last_reason": self._last_reason,
            }
            merged = {**payload, **extras}
            if self.events_tail is not None:
                try:
                    merged["events"] = self.events_tail()
                except Exception:
                    merged["events"] = []
            self._status = merged

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def request_ring_test(self) -> bool:
        """Queue a ring test. Returns False if one already pending."""
        with self._lock:
            if self._ring_request:
                return False
            self._ring_request = True
            return True

    def take_ring_test(self) -> bool:
        with self._lock:
            if not self._ring_request:
                return False
            self._ring_request = False
            return True

    def request_place_call(self, e164: str) -> bool:
        """Queue an outbound call (console callback / phonebook). One pending."""
        dest = (e164 or "").strip()
        if not dest:
            return False
        with self._lock:
            if self._place_call:
                return False
            self._place_call = dest
            return True

    def take_place_call(self) -> str | None:
        with self._lock:
            dest = self._place_call
            self._place_call = None
            return dest

    def login(self, password: str) -> str | None:
        expected = console_password()
        if not expected:
            return None
        if not hmac.compare_digest(password.encode(), expected.encode()):
            return None
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[token] = time.monotonic() + self._session_ttl_s
        return token

    def logout(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(token, None)

    def authed(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            exp = self._sessions.get(token)
            if exp is None:
                return False
            if time.monotonic() > exp:
                self._sessions.pop(token, None)
                return False
            return True


def compute_readiness(
    *,
    sip_wanted: bool,
    sip_registered: bool,
    calendar_wanted: bool,
    calendar_ok: bool,
) -> dict[str, Any]:
    """Few explicit rules — READY vs DEGRADED."""
    reasons: list[str] = []
    if sip_wanted and not sip_registered:
        reasons.append("sip_not_registered")
    if calendar_wanted and not calendar_ok:
        reasons.append("calendar_not_linked")
    level = "DEGRADED" if reasons else "READY"
    return {"level": level, "reasons": reasons}


def digit_menu_tree() -> list[dict[str, Any]]:
    """Hierarchical guide for the console (mirrors services + streams map)."""
    from operator_os.streams import load_streams

    streams = load_streams()
    s3 = streams.get("3", {})
    s4 = streams.get("4", {})
    return [
        {
            "digit": 0,
            "label": "Operator menu",
            "kind": "speak",
            "highlight_states": ["DIAL_TONE", "COLLECTING_DIGIT"],
        },
        {
            "digit": 1,
            "label": "News of the Day",
            "kind": "play_file",
            "highlight_kinds": ["play_file", "speak"],
            "service_digit": 1,
        },
        {
            "digit": 2,
            "label": "Weather Bureau",
            "kind": "play_file",
            "service_digit": 2,
        },
        {
            "digit": 3,
            "label": s3.get("label") or "WAMU 88.5",
            "kind": "stream",
            "service_digit": 3,
            "url": s3.get("url") or "",
        },
        {
            "digit": 4,
            "label": s4.get("label") or "NWS weather radio",
            "kind": "stream",
            "service_digit": 4,
            "url": s4.get("url") or "",
        },
        {
            "digit": 5,
            "label": "Messages / mailbox",
            "kind": "mailbox",
            "service_digit": 5,
        },
        {
            "digit": 7,
            "label": "Join Meet",
            "kind": "join_meeting",
            "highlight_states": ["MEET_CHOOSING", "SIP_CALL"],
            "children": [
                {"label": "Resolve calendars", "note": "unique / RSVP / menu"},
                {"label": "MEET_CHOOSING", "note": "dial 1–N"},
                {"label": "SIP_CALL", "note": "dial + PIN"},
            ],
        },
        {
            "digit": 8,
            "label": "Information desk",
            "kind": "info_desk",
            "service_digit": 8,
        },
        {
            "digit": 9,
            "label": "Outside line",
            "kind": "outside_seize",
            "highlight_states": ["OUTSIDE_LINE", "SIP_CALL"],
            "children": [
                {"label": "OUTSIDE_LINE", "note": "number or short code"},
                {"label": "place_call", "note": "→ SIP_CALL"},
            ],
        },
    ]


def chart_edges_json() -> dict[str, Any]:
    from operator_os.state import CHART_EDGES, State

    edges = [
        {
            "source": e.source.value,
            "event": e.event,
            "dest": e.dest.value,
            "actions": list(e.actions),
        }
        for e in CHART_EDGES
    ]
    return {
        "states": [s.value for s in State],
        "edges": edges,
    }


def session_cookie_name() -> str:
    return "operator_console_session"


def hash_token_for_log(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:12]
