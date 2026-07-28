"""LAN diagnostic console HTTP server (Phase A + B)."""

from __future__ import annotations

import json
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from operator_os.console_hub import (
    ConsoleHub,
    chart_edges_json,
    console_bind,
    console_password,
    console_port,
    digit_menu_tree,
    session_cookie_name,
)

STATIC_DIR = Path(__file__).resolve().parent / "console_static"
ROOT = Path(__file__).resolve().parents[1]
VM_DIR = (ROOT / "data" / "voicemail").resolve()


class ConsoleHttpServer:
    def __init__(self, hub: ConsoleHub, *, host: str | None = None, port: int | None = None):
        self.hub = hub
        self.host = host if host is not None else console_bind()
        self.port = port if port is not None else console_port()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._httpd is not None:
            return
        hub = self.hub

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: Any) -> None:
                print(f"console: {self.address_string()} {fmt % args}", flush=True)

            def _token(self) -> str | None:
                raw = self.headers.get("Cookie", "")
                jar = SimpleCookie()
                try:
                    jar.load(raw)
                except Exception:
                    return None
                morsel = jar.get(session_cookie_name())
                return morsel.value if morsel else None

            def _ok_json(self, obj: Any, *, code: int = 200) -> None:
                body = json.dumps(obj, separators=(",", ":"), default=str).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _err(self, code: int, msg: str) -> None:
                self._ok_json({"error": msg}, code=code)

            def _require_auth(self) -> bool:
                if hub.authed(self._token()):
                    return True
                self._err(401, "unauthorized")
                return False

            def _read_json(self) -> dict[str, Any]:
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b"{}"
                try:
                    data = json.loads(raw.decode() or "{}")
                except json.JSONDecodeError:
                    return {}
                return data if isinstance(data, dict) else {}

            def _serve_static(self, rel: str) -> None:
                if ".." in rel or rel.startswith("/"):
                    self._err(400, "bad path")
                    return
                path = (STATIC_DIR / (rel or "index.html")).resolve()
                if not str(path).startswith(str(STATIC_DIR.resolve())):
                    self._err(400, "bad path")
                    return
                if path.is_dir():
                    path = path / "index.html"
                if not path.is_file():
                    self._err(404, "not found")
                    return
                data = path.read_bytes()
                ctype = "text/plain"
                if path.suffix == ".html":
                    ctype = "text/html; charset=utf-8"
                elif path.suffix == ".css":
                    ctype = "text/css; charset=utf-8"
                elif path.suffix == ".js":
                    ctype = "application/javascript; charset=utf-8"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _serve_bytes(self, data: bytes, ctype: str) -> None:
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802
                path = urlparse(self.path).path
                if path in ("/", "/index.html"):
                    self._serve_static("index.html")
                    return
                if path.startswith("/static/"):
                    self._serve_static(path[len("/static/") :])
                    return
                if path == "/api/whoami":
                    self._ok_json({"ok": hub.authed(self._token())})
                    return
                if path == "/api/chart":
                    if not self._require_auth():
                        return
                    self._ok_json(chart_edges_json())
                    return
                if path == "/api/menu":
                    if not self._require_auth():
                        return
                    self._ok_json({"digits": digit_menu_tree()})
                    return
                if path == "/api/status":
                    if not self._require_auth():
                        return
                    self._ok_json(hub.status())
                    return
                if path == "/api/inbox":
                    if not self._require_auth():
                        return
                    self._ok_json(_inbox_payload())
                    return
                if path.startswith("/api/inbox/vm/") and path.endswith("/audio"):
                    if not self._require_auth():
                        return
                    mid = path[len("/api/inbox/vm/") : -len("/audio")].strip("/")
                    if not mid.isdigit():
                        self._err(400, "bad id")
                        return
                    self._serve_vm_audio(int(mid))
                    return
                if path == "/api/phonebook":
                    if not self._require_auth():
                        return
                    from operator_os.phonebook import contact_to_dict, list_contacts

                    self._ok_json({"contacts": [contact_to_dict(c) for c in list_contacts()]})
                    return
                if path == "/api/streams":
                    if not self._require_auth():
                        return
                    from operator_os.streams import load_streams, streams_path

                    self._ok_json({"streams": load_streams(), "path": str(streams_path())})
                    return
                self._err(404, "not found")

            def _serve_vm_audio(self, vm_id: int) -> None:
                from operator_os import db as store

                vm = store.get_voicemail(vm_id)
                if vm is None:
                    self._err(404, "not found")
                    return
                path = Path(vm.path).resolve()
                if not str(path).startswith(str(VM_DIR)) or not path.is_file():
                    self._err(404, "audio missing")
                    return
                self._serve_bytes(path.read_bytes(), "audio/wav")

            def do_POST(self) -> None:  # noqa: N802
                path = urlparse(self.path).path
                if path == "/api/login":
                    n = int(self.headers.get("Content-Length") or 0)
                    raw = self.rfile.read(n) if n else b""
                    pw = ""
                    ctype = (self.headers.get("Content-Type") or "").lower()
                    if "json" in ctype:
                        try:
                            data = json.loads(raw.decode() or "{}")
                            if isinstance(data, dict):
                                pw = str(data.get("password") or "")
                        except json.JSONDecodeError:
                            pw = ""
                    else:
                        qs = parse_qs(raw.decode(errors="replace"))
                        pw = (qs.get("password") or [""])[0]
                    if not console_password():
                        self._err(503, "console password not configured")
                        return
                    token = hub.login(pw)
                    if token is None:
                        self._err(403, "bad password")
                        return
                    body = json.dumps({"ok": True}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header(
                        "Set-Cookie",
                        f"{session_cookie_name()}={token}; Path=/; HttpOnly; SameSite=Strict",
                    )
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if path == "/api/logout":
                    hub.logout(self._token() or "")
                    body = b'{"ok":true}'
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header(
                        "Set-Cookie",
                        f"{session_cookie_name()}=; Path=/; Max-Age=0",
                    )
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if not self._require_auth():
                    return
                if path == "/api/ring-test":
                    if hub.request_ring_test():
                        from operator_os.console_hub import RING_TEST_MS

                        self._ok_json({"ok": True, "ms": RING_TEST_MS})
                    else:
                        self._err(409, "ring test already pending")
                    return
                if path == "/api/place-call":
                    data = self._read_json()
                    e164 = str(data.get("e164") or "").strip()
                    if not e164:
                        name = str(data.get("name") or "").strip()
                        if name:
                            from operator_os.phonebook import lookup_by_name

                            c = lookup_by_name(name)
                            if c is None:
                                self._err(404, "name not found")
                                return
                            e164 = c.e164
                    from operator_os.sip import normalize_nanp

                    dest = normalize_nanp(e164) or e164
                    if not hub.request_place_call(dest):
                        self._err(409, "place call already pending")
                        return
                    self._ok_json({"ok": True, "e164": dest})
                    return
                if path == "/api/inbox/sms/heard":
                    data = self._read_json()
                    from operator_os import db as store

                    m = store.mark_heard(int(data.get("id") or 0))
                    self._ok_json({"ok": m is not None})
                    return
                if path == "/api/inbox/sms/delete":
                    data = self._read_json()
                    from operator_os import db as store

                    self._ok_json({"ok": store.delete_message(int(data.get("id") or 0))})
                    return
                if path == "/api/inbox/sms/reply":
                    self._sms_reply(self._read_json())
                    return
                if path == "/api/inbox/vm/heard":
                    data = self._read_json()
                    from operator_os import db as store

                    m = store.mark_voicemail_heard(int(data.get("id") or 0))
                    self._ok_json({"ok": m is not None})
                    return
                if path == "/api/inbox/vm/delete":
                    data = self._read_json()
                    from operator_os import db as store

                    self._ok_json({"ok": store.delete_voicemail(int(data.get("id") or 0))})
                    return
                if path == "/api/phonebook":
                    data = self._read_json()
                    from operator_os.phonebook import contact_to_dict, upsert_contact

                    try:
                        c = upsert_contact(
                            name=str(data.get("name") or ""),
                            e164=str(data.get("e164") or ""),
                            short_code=str(data.get("short_code") or ""),
                            notes=str(data.get("notes") or ""),
                            contact_id=int(data["id"]) if data.get("id") is not None else None,
                        )
                    except ValueError as e:
                        self._err(400, str(e))
                        return
                    self._ok_json({"ok": True, "contact": contact_to_dict(c)})
                    return
                if path == "/api/phonebook/delete":
                    data = self._read_json()
                    from operator_os.phonebook import delete_contact

                    self._ok_json({"ok": delete_contact(int(data.get("id") or 0))})
                    return
                if path == "/api/streams":
                    data = self._read_json()
                    from operator_os.streams import save_streams

                    try:
                        out = save_streams(data.get("streams") or data)
                    except ValueError as e:
                        self._err(400, str(e))
                        return
                    self._ok_json({"ok": True, "streams": out})
                    return
                self._err(404, "not found")

            def _sms_reply(self, data: dict[str, Any]) -> None:
                from operator_os import db as store
                from operator_os.sip import normalize_nanp
                from operator_os.sms import send_sms, sms_configured, sms_from

                if not data.get("confirm"):
                    self._err(400, "confirm required")
                    return
                if not sms_configured():
                    self._err(503, "sms not configured")
                    return
                to = str(data.get("to") or "").strip()
                mid = data.get("id")
                if mid is not None and not to:
                    msg = store.get_message(int(mid))
                    if msg is None:
                        self._err(404, "message not found")
                        return
                    to = msg.from_e164
                dest = normalize_nanp(to)
                if not dest:
                    self._err(400, "invalid to")
                    return
                text = str(data.get("text") or "").strip()
                if not text or len(text) > 500:
                    self._err(400, "text required (1–500 chars)")
                    return
                try:
                    sent = send_sms(to=dest, text=text)
                except Exception as e:
                    self._err(502, str(e))
                    return
                store.insert_outbound(
                    to_e164=sent.to_e164,
                    from_e164=sent.from_e164 or sms_from() or "",
                    body=sent.body,
                    telnyx_id=sent.telnyx_id,
                )
                self._ok_json({"ok": True, "to": dest})

        self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        self._thread = None


def _inbox_payload() -> dict[str, Any]:
    from operator_os import db as store
    from operator_os.phonebook import display_name

    store.init_db()
    sms = []
    for m in store.list_messages(limit=40):
        sms.append(
            {
                "id": m.id,
                "direction": m.direction,
                "from_e164": m.from_e164,
                "to_e164": m.to_e164,
                "from_name": display_name(m.from_e164) if m.direction == "in" else None,
                "to_name": display_name(m.to_e164) if m.direction == "out" else None,
                "body": m.body,
                "created_at": m.created_at,
                "heard_at": m.heard_at,
                "status": m.status,
            }
        )
    vms = []
    for vm in store.list_voicemails(limit=40):
        vms.append(
            {
                "id": vm.id,
                "from_e164": vm.from_e164,
                "from_name": display_name(vm.from_e164),
                "created_at": vm.created_at,
                "heard_at": vm.heard_at,
                "duration_s": vm.duration_s,
                "status": vm.status,
                "audio_url": f"/api/inbox/vm/{vm.id}/audio",
            }
        )
    return {
        "sms": sms,
        "voicemails": vms,
        "waiting": store.waiting_count(),
    }
