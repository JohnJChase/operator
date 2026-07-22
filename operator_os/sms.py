"""Telnyx SMS send + local inbound webhook (Block 4)."""

from __future__ import annotations

import base64
import json
import os
import queue
import re
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from operator_os import db as store
from operator_os.sip import normalize_nanp

TELNYX_MESSAGES_URL = "https://api.telnyx.com/v2/messages"
DEFAULT_WEBHOOK_PORT = 8787
WEBHOOK_PATH = "/webhooks/telnyx/sms"


def api_key() -> str:
    return os.environ.get("TELNYX_API_KEY", "").strip()


def sms_from() -> str:
    return (
        os.environ.get("TELNYX_SMS_FROM", "").strip()
        or os.environ.get("TELNYX_CALLER_ID", "").strip()
    )


def messaging_profile_id() -> str:
    return os.environ.get("TELNYX_MESSAGING_PROFILE_ID", "").strip()


def public_key() -> str:
    return os.environ.get("TELNYX_PUBLIC_KEY", "").strip()


def inject_token() -> str:
    """Shared secret for unsigned local sms-inject (Funnel still requires Telnyx sig)."""
    return os.environ.get("OPERATOR_SMS_INJECT_TOKEN", "local-dev").strip() or "local-dev"


def _log_outbound_status(payload: dict[str, Any]) -> None:
    """Journal delivery receipts (API accept ≠ handset delivery)."""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return
    inner = data.get("payload") if isinstance(data.get("payload"), dict) else data
    if not isinstance(inner, dict):
        return
    if str(inner.get("direction") or "").lower() not in ("outbound", "out"):
        return
    mid = str(inner.get("id") or "")[:40]
    errs = inner.get("errors") or []
    to = inner.get("to")
    status = ""
    if isinstance(to, list) and to and isinstance(to[0], dict):
        status = str(to[0].get("status") or "")
    detail = ""
    if isinstance(errs, list) and errs and isinstance(errs[0], dict):
        detail = str(errs[0].get("detail") or errs[0].get("title") or "")[:120]
    if status or detail:
        print(f"sms: outbound id={mid} status={status or '?'} {detail}".rstrip(), flush=True)


INJECT_HEADER = "X-Operator-Inject-Token"


def webhook_port() -> int:
    raw = os.environ.get("OPERATOR_SMS_WEBHOOK_PORT", str(DEFAULT_WEBHOOK_PORT)).strip()
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_WEBHOOK_PORT


def sms_configured() -> bool:
    return bool(api_key() and sms_from())


def _normalize_dest(raw: str) -> str | None:
    return normalize_nanp(raw) or None


@dataclass(frozen=True)
class SendResult:
    telnyx_id: str
    to_e164: str
    from_e164: str
    body: str


def send_sms(to: str, text: str) -> SendResult:
    """POST /v2/messages. Raises RuntimeError on failure."""
    key = api_key()
    frm = sms_from()
    if not key or not frm:
        raise RuntimeError("SMS not configured (TELNYX_API_KEY + from number)")
    dest = _normalize_dest(to)
    if dest is None:
        raise RuntimeError("Invalid destination number")
    body_text = (text or "").strip()
    if not body_text:
        raise RuntimeError("Empty message")
    if len(body_text) > 1600:
        body_text = body_text[:1600]
    payload: dict[str, Any] = {
        "from": frm if frm.startswith("+") else (normalize_nanp(frm) or frm),
        "to": dest,
        "text": body_text,
        "type": "SMS",
    }
    mid = messaging_profile_id()
    if mid:
        payload["messaging_profile_id"] = mid
    data = json.dumps(payload).encode()
    req = Request(TELNYX_MESSAGES_URL, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    try:
        with urlopen(req, timeout=30) as resp:
            raw = json.loads(resp.read().decode())
    except HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"Telnyx send failed: {detail}") from e
    except URLError as e:
        raise RuntimeError(f"Telnyx network error: {e}") from e
    msg = (raw.get("data") or {}) if isinstance(raw, dict) else {}
    tid = str(msg.get("id") or "").strip()
    if not tid:
        raise RuntimeError(f"Telnyx send returned no id: {raw!r}"[:300])
    return SendResult(
        telnyx_id=tid,
        to_e164=dest,
        from_e164=str(payload["from"]),
        body=body_text,
    )


def verify_ed25519(
    *,
    raw_body: bytes,
    signature_b64: str,
    timestamp: str,
    public_key_b64: str,
    max_age_s: float = 300.0,
) -> bool:
    """Verify Telnyx webhook: sign over ``{timestamp}|{payload}``."""
    if not signature_b64 or not timestamp or not public_key_b64:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs(time.time() - ts) > max_age_s:
        return False
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as e:
        raise RuntimeError(
            "cryptography package required for Telnyx webhook signature verify"
        ) from e
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
        signed = f"{timestamp}|".encode() + raw_body
        key.verify(base64.b64decode(signature_b64), signed)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def parse_inbound_webhook(payload: dict[str, Any]) -> dict[str, str] | None:
    """Extract inbound SMS fields from a Telnyx v2 webhook JSON object.

    Returns dict with telnyx_id, from_e164, to_e164, body — or None if not inbound SMS.
    """
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return None
    event_type = str(data.get("event_type") or payload.get("event_type") or "").lower()
    # Nested payload shape: data.payload
    inner = data.get("payload") if isinstance(data.get("payload"), dict) else data
    if not isinstance(inner, dict):
        return None
    direction = str(inner.get("direction") or "").lower()
    if event_type and "message" in event_type and "received" not in event_type:
        # delivery updates etc.
        if direction != "inbound":
            return None
    if direction and direction not in ("inbound", "in"):
        if "received" not in event_type:
            return None
    text = str(inner.get("text") or inner.get("body") or "").strip()
    from_raw = ""
    to_raw = ""
    frm = inner.get("from")
    if isinstance(frm, dict):
        from_raw = str(frm.get("phone_number") or frm.get("number") or "")
    else:
        from_raw = str(frm or "")
    to = inner.get("to")
    if isinstance(to, list) and to:
        first = to[0]
        if isinstance(first, dict):
            to_raw = str(first.get("phone_number") or first.get("number") or "")
        else:
            to_raw = str(first)
    elif isinstance(to, dict):
        to_raw = str(to.get("phone_number") or to.get("number") or "")
    else:
        to_raw = str(to or "")
    tid = str(inner.get("id") or data.get("id") or "").strip()
    if not tid or not text:
        return None
    # Accept missing direction if event says message.received
    if direction and direction not in ("inbound", "in"):
        return None
    if not direction and "received" not in event_type and event_type:
        return None
    from_e164 = normalize_nanp(from_raw) or re.sub(r"[^\d+]", "", from_raw)
    to_e164 = normalize_nanp(to_raw) or re.sub(r"[^\d+]", "", to_raw) or sms_from()
    return {
        "telnyx_id": tid,
        "from_e164": from_e164,
        "to_e164": to_e164,
        "body": text,
    }


NotifyFn = Callable[[int], None]


class SmsWebhookServer:
    """127.0.0.1 webhook → SQLite upsert → notify main loop."""

    def __init__(
        self,
        *,
        port: int | None = None,
        on_message: NotifyFn | None = None,
        require_signature: bool | None = None,
    ) -> None:
        self.port = port if port is not None else webhook_port()
        self.on_message = on_message
        self.require_signature = (
            require_signature if require_signature is not None else bool(public_key())
        )
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._httpd is not None:
            return
        store.init_db()
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                if self.path.split("?", 1)[0].rstrip("/") != WEBHOOK_PATH.rstrip("/"):
                    self.send_error(404)
                    return
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                sig = self.headers.get("telnyx-signature-ed25519", "").strip()
                ts = self.headers.get("telnyx-timestamp", "").strip()
                if sig or ts:
                    # Live Telnyx (via Funnel): verify when public key is configured.
                    if public_key():
                        try:
                            ok = verify_ed25519(
                                raw_body=raw,
                                signature_b64=sig,
                                timestamp=ts,
                                public_key_b64=public_key(),
                            )
                        except RuntimeError as e:
                            self.send_error(500, str(e))
                            return
                        if not ok:
                            self.send_error(403, "bad signature")
                            return
                elif public_key():
                    # Unsigned: local sms-inject only (not Telnyx).
                    if self.headers.get(INJECT_HEADER, "") != inject_token():
                        self.send_error(403, "inject token required")
                        return
                try:
                    payload = json.loads(raw.decode("utf-8") if raw else "{}")
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self.send_error(400, "invalid json")
                    return
                if not isinstance(payload, dict):
                    self.send_error(400, "invalid json")
                    return
                # Inject helper shape: {telnyx_id, from, to, text}
                parsed = parse_inbound_webhook(payload)
                if parsed is None and payload.get("text") and payload.get("from"):
                    parsed = {
                        "telnyx_id": str(
                            payload.get("telnyx_id")
                            or payload.get("id")
                            or f"inject-{time.time_ns()}"
                        ),
                        "from_e164": normalize_nanp(str(payload["from"]))
                        or str(payload["from"]),
                        "to_e164": normalize_nanp(str(payload.get("to") or sms_from() or ""))
                        or str(payload.get("to") or ""),
                        "body": str(payload["text"]),
                    }
                if parsed is None:
                    # Ack delivery receipts so Telnyx stops retrying.
                    _log_outbound_status(payload)
                    self.send_response(200)
                    self.send_header("Content-Length", "2")
                    self.end_headers()
                    self.wfile.write(b"ok")
                    return
                msg, created = store.upsert_inbound(
                    telnyx_id=parsed["telnyx_id"],
                    from_e164=parsed["from_e164"],
                    to_e164=parsed["to_e164"],
                    body=parsed["body"],
                )
                if created and server.on_message is not None:
                    try:
                        server.on_message(msg.id)
                    except Exception:
                        pass
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                return

        self._httpd = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        httpd = self._httpd
        self._httpd = None
        if httpd is not None:
            httpd.shutdown()


def attach_notify_queue(q: queue.SimpleQueue) -> NotifyFn:
    def _notify(message_id: int) -> None:
        q.put(("sms", int(message_id)))

    return _notify
