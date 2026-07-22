"""Google Calendar read + OAuth refresh for Meet dial-in (digit 7)."""

from __future__ import annotations

import json
import os
import re
import threading
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
CAL_API = "https://www.googleapis.com/calendar/v3"


@dataclass(frozen=True)
class MeetDialIn:
    title: str
    e164: str
    pin: str = ""
    event_id: str = ""


def client_id() -> str:
    return os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()


def client_secret() -> str:
    return os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()


def refresh_token() -> str:
    return os.environ.get("GOOGLE_OAUTH_REFRESH_TOKEN", "").strip()


def calendar_id() -> str:
    return os.environ.get("GOOGLE_CALENDAR_ID", "primary").strip() or "primary"


def calendar_configured() -> bool:
    return bool(client_id() and client_secret() and refresh_token())


def _upsert_env(key: str, value: str, path: Path = ENV_PATH) -> None:
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    line = f"{key}={value}"
    if re.search(rf"(?m)^{re.escape(key)}=", text):
        text = re.sub(rf"(?m)^{re.escape(key)}=.*$", line, text)
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += line + "\n"
    path.write_text(text, encoding="utf-8")
    os.environ[key] = value


def access_token() -> str:
    """Exchange refresh token for a short-lived access token."""
    rt = refresh_token()
    if not rt:
        raise RuntimeError("GOOGLE_OAUTH_REFRESH_TOKEN missing — run: just calendar-auth")
    body = urlencode(
        {
            "client_id": client_id(),
            "client_secret": client_secret(),
            "refresh_token": rt,
            "grant_type": "refresh_token",
        }
    ).encode()
    req = Request(TOKEN_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:240]
        raise RuntimeError(f"Google token refresh failed: {detail}") from e
    token = (data.get("access_token") or "").strip()
    if not token:
        raise RuntimeError("Google token refresh returned no access_token")
    return token


def _get_json(url: str, token: str) -> dict[str, Any]:
    req = Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:240]
        raise RuntimeError(f"Calendar API {e.code}: {detail}") from e
    except URLError as e:
        raise RuntimeError(f"Calendar network error: {e}") from e


def list_events_around_now(
    *,
    lookback_min: int = 15,
    lookahead_min: int = 30,
    max_results: int = 10,
) -> list[dict[str, Any]]:
    """Primary calendar events overlapping [now-lookback, now+lookahead]."""
    token = access_token()
    now = datetime.now(timezone.utc)
    time_min = (now - timedelta(minutes=lookback_min)).isoformat().replace("+00:00", "Z")
    time_max = (now + timedelta(minutes=lookahead_min)).isoformat().replace("+00:00", "Z")
    q = urlencode(
        {
            "timeMin": time_min,
            "timeMax": time_max,
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": str(max_results),
            "conferenceDataVersion": "1",
        }
    )
    url = f"{CAL_API}/calendars/{quote(calendar_id(), safe='')}/events?{q}"
    data = _get_json(url, token)
    return list(data.get("items") or [])


def _e164_from_tel_uri(uri: str) -> tuple[str, str]:
    """Parse tel:+1…,,,,PIN into (e164, pin)."""
    raw = (uri or "").strip()
    if raw.lower().startswith("tel:"):
        raw = raw[4:]
    pin = ""
    if "," in raw:
        main, *rest = raw.split(",")
        pin = re.sub(r"\D", "", "".join(rest))
        raw = main
    digits = re.sub(r"[^\d+]", "", raw)
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if digits.isdigit() and len(digits) == 10:
        digits = "+1" + digits
    if digits.isdigit() and len(digits) == 11 and digits.startswith("1"):
        digits = "+" + digits
    if not digits.startswith("+"):
        only = re.sub(r"\D", "", digits)
        if len(only) == 10:
            digits = "+1" + only
        elif len(only) >= 8:
            digits = "+" + only
        else:
            return "", pin
    return digits, pin


def _phone_candidates(event: dict[str, Any]) -> list[tuple[str, str, bool]]:
    """Return (e164, pin, prefer_us) for each phone entry point."""
    conf = event.get("conferenceData") or {}
    out: list[tuple[str, str, bool]] = []
    for ep in conf.get("entryPoints") or []:
        if (ep.get("entryPointType") or "").lower() != "phone":
            continue
        e164, pin_from_uri = _e164_from_tel_uri(ep.get("uri") or "")
        if not e164:
            continue
        pin = (
            ep.get("pin") or ep.get("accessCode") or ep.get("passcode") or pin_from_uri or ""
        ).strip()
        pin = re.sub(r"\D", "", pin)
        region = (ep.get("regionCode") or "").strip().upper()
        # Calendar often returns only the organizer's country; US must win when present.
        prefer_us = region == "US"
        out.append((e164, pin, prefer_us))
    return out


def _more_tel_uri(event: dict[str, Any]) -> str:
    conf = event.get("conferenceData") or {}
    for ep in conf.get("entryPoints") or []:
        if (ep.get("entryPointType") or "").lower() != "more":
            continue
        uri = (ep.get("uri") or "").strip()
        if "tel.meet" in uri or "/tel/" in uri:
            return uri
    cid = (conf.get("conferenceId") or "").strip()
    if cid:
        return f"https://tel.meet/{cid}"
    return ""


def _meet_pin(event: dict[str, Any]) -> str:
    """PIN for PSTN join.

    Calendar's ``phone`` entry often has a short regional PIN that Meet rejects;
    the ``more`` / tel.meet entry carries the real meeting PIN (longer).
    """
    conf = event.get("conferenceData") or {}
    more_pin = ""
    phone_pin = ""
    for ep in conf.get("entryPoints") or []:
        kind = (ep.get("entryPointType") or "").lower()
        raw = (ep.get("pin") or ep.get("accessCode") or ep.get("passcode") or "").strip()
        pin = re.sub(r"\D", "", raw)
        if kind == "more":
            if not pin:
                qs = parse_qs(urlparse(ep.get("uri") or "").query)
                pin = re.sub(r"\D", "", (qs.get("pin") or [""])[0])
            if pin:
                more_pin = pin
        elif kind == "phone" and pin and not phone_pin:
            phone_pin = pin
    return more_pin or phone_pin


_US_MEET_ROW = re.compile(
    r'\["(\+\d+)","United States","[^"]*",\d+,"US",\d+\]'
)


def us_e164_from_tel_meet_html(html: str) -> str:
    """Parse United States dial-in from a tel.meet / Meet dial-in HTML page."""
    m = _US_MEET_ROW.search(html or "")
    if not m:
        return ""
    e164, _ = _e164_from_tel_uri(m.group(1))
    return e164


def fetch_us_meet_number(more_uri: str) -> str:
    """Load Meet's multi-region dial-in page and return the US E.164 number."""
    uri = (more_uri or "").strip()
    if not uri:
        return ""
    if uri.startswith("https://tel.meet/"):
        # Public dial-in list also lives under meet.google.com/tel/…
        uri = "https://meet.google.com/tel/" + uri[len("https://tel.meet/") :]
    req = Request(uri, method="GET")
    req.add_header("User-Agent", "WE302-Operator/1.0")
    try:
        with urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError, OSError):
        return ""
    return us_e164_from_tel_meet_html(html)


def extract_meet_dial_in(event: dict[str, Any]) -> MeetDialIn | None:
    """Pick a Meet phone dial-in; always prefer a US number when possible."""
    title = (event.get("summary") or "Meeting").strip()
    candidates = _phone_candidates(event)
    if not candidates:
        return None
    pin = _meet_pin(event) or next((c[1] for c in candidates if c[1]), "")
    us = [c for c in candidates if c[2]]
    if us:
        e164, _, _ = us[0]
        return MeetDialIn(
            title=title,
            e164=e164,
            pin=pin,
            event_id=str(event.get("id") or ""),
        )
    # Calendar API often returns only the organizer region (e.g. GB). The `more`
    # / tel.meet page lists every country — pull United States from there.
    us_e164 = fetch_us_meet_number(_more_tel_uri(event))
    if us_e164:
        return MeetDialIn(
            title=title,
            e164=us_e164,
            pin=pin,
            event_id=str(event.get("id") or ""),
        )
    e164, _, _ = candidates[0]
    return MeetDialIn(
        title=title,
        e164=e164,
        pin=pin,
        event_id=str(event.get("id") or ""),
    )


def find_joinable_meetings() -> list[MeetDialIn]:
    out: list[MeetDialIn] = []
    for ev in list_events_around_now():
        dial = extract_meet_dial_in(ev)
        if dial is not None:
            out.append(dial)
    return out


def pick_meeting_to_join() -> tuple[MeetDialIn | None, str]:
    """Return (meeting, reason). reason is empty on success."""
    found = find_joinable_meetings()
    if not found:
        return None, "No meeting with a phone dial-in was found."
    if len(found) > 1:
        return None, "Several meetings have dial-in numbers. Join from the calendar."
    return found[0], ""


def run_calendar_auth(*, open_browser: bool = True) -> int:
    """Desktop OAuth: local redirect → write GOOGLE_OAUTH_REFRESH_TOKEN into .env."""
    cid, csec = client_id(), client_secret()
    if not cid or not csec:
        print("Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET in .env first.")
        return 1

    code_holder: dict[str, str] = {}
    ready = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            qs = parse_qs(urlparse(self.path).query)
            if "code" in qs:
                code_holder["code"] = qs["code"][0]
                body = (
                    b"<html><body><h1>WE302 Operator</h1>"
                    b"<p>Calendar linked. You can close this tab.</p></body></html>"
                )
                self.send_response(200)
            else:
                err = qs.get("error", ["unknown"])[0]
                code_holder["error"] = err
                body = f"<html><body><h1>Auth failed</h1><p>{err}</p></body></html>".encode()
                self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            ready.set()

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    redirect = f"http://127.0.0.1:{port}/"
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    params = {
        "client_id": cid,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
    }
    url = f"{AUTH_URL}?{urlencode(params)}"
    print("Open this URL in a browser (calendar owner account):")
    print(url)
    print()
    print(f"(Listening for redirect on {redirect})")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    if not ready.wait(timeout=300):
        httpd.shutdown()
        print("Timed out waiting for Google consent.")
        return 1
    httpd.shutdown()
    if "error" in code_holder:
        print(f"Google auth error: {code_holder['error']}")
        return 1
    code = code_holder.get("code")
    if not code:
        print("No authorization code received.")
        return 1

    body = urlencode(
        {
            "code": code,
            "client_id": cid,
            "client_secret": csec,
            "redirect_uri": redirect,
            "grant_type": "authorization_code",
        }
    ).encode()
    req = Request(TOKEN_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        print(f"Token exchange failed: {detail}")
        return 1

    rt = (data.get("refresh_token") or "").strip()
    if not rt:
        print(
            "No refresh_token in response. Revoke the app under "
            "https://myaccount.google.com/permissions and retry."
        )
        return 1
    _upsert_env("GOOGLE_OAUTH_REFRESH_TOKEN", rt)
    print("Wrote GOOGLE_OAUTH_REFRESH_TOKEN to .env")
    try:
        items = list_events_around_now()
        print(f"Calendar OK — {len(items)} event(s) in the near window.")
        for dial in find_joinable_meetings()[:3]:
            extra = f" pin={dial.pin}" if dial.pin else ""
            print(f"  meet dial-in: {dial.title!r} → {dial.e164}{extra}")
    except Exception as e:
        print(f"Token saved but calendar probe failed: {e}")
        return 1
    return 0
