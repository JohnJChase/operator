"""Mac companion client for the Operator desktop bridge."""

from __future__ import annotations

import argparse
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any
from urllib.parse import urlencode, urljoin

from operator_os.desktop_bridge import validate_open_url


CAPABILITIES = ["open_url", "notify"]


def run_mac_client(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="operator-os mac-client",
        description="Connect this Mac to a WE302 Operator Pi.",
    )
    parser.add_argument(
        "--pi-url",
        default=os.environ.get("OPERATOR_PI_URL", "http://operator.local:8788"),
        help="Pi console URL, e.g. http://operator.local:8788",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("OPERATOR_DESKTOP_TOKEN", ""),
        help="shared OPERATOR_DESKTOP_TOKEN",
    )
    parser.add_argument(
        "--client-id",
        default=os.environ.get("OPERATOR_DESKTOP_CLIENT_ID", socket.gethostname()),
        help="stable desktop client id",
    )
    parser.add_argument(
        "--name",
        default=os.environ.get("OPERATOR_DESKTOP_NAME", f"{socket.gethostname()} Mac"),
        help="display name shown by the Pi",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=_truthy(os.environ.get("OPERATOR_DESKTOP_VERBOSE", "")),
        help="log SSE keepalives while waiting for commands",
    )
    parser.add_argument(
        "--notify-mode",
        choices=("notification", "alert", "both"),
        default=_notify_mode(os.environ.get("OPERATOR_DESKTOP_NOTIFY_MODE", "")),
        help="macOS presentation: notification banner, visible alert, or both",
    )
    parser.add_argument("--once", action="store_true", help="connect once; do not retry")
    args = parser.parse_args(argv)

    if not args.token.strip():
        print("OPERATOR_DESKTOP_TOKEN is required.", file=sys.stderr)
        return 2
    base = args.pi_url.rstrip("/")
    while True:
        try:
            _register(base, args.token, args.client_id, args.name)
            _event_loop(
                base,
                args.token,
                args.client_id,
                verbose=args.verbose,
                notify_mode=args.notify_mode,
            )
        except KeyboardInterrupt:
            print("mac-client: quit")
            return 0
        except Exception as e:
            print(f"mac-client: disconnected: {e}", file=sys.stderr)
            if args.once:
                return 1
            time.sleep(2.0)


def run_mac_status(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="operator-os mac-status",
        description="Show this Mac's view of the Operator Pi.",
    )
    _add_console_args(parser)
    parser.add_argument("--json", action="store_true", help="print raw status JSON")
    args = parser.parse_args(argv)

    try:
        opener = _mac_api_opener(args)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    base = args.pi_url.rstrip("/")
    try:
        status = _get_json(opener, base, "/api/status")
    except Exception as e:
        print(f"mac-status: {e}", file=sys.stderr)
        return 1
    print(json.dumps(status, indent=2) if args.json else _format_status(status))
    return 0


def run_mac_inbox(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="operator-os mac-inbox",
        description="List Operator SMS and voicemail from this Mac.",
    )
    _add_console_args(parser)
    parser.add_argument("--limit", type=int, default=10, help="items to show per section")
    parser.add_argument("--json", action="store_true", help="print raw inbox JSON")
    parser.add_argument("--play-vm", type=int, default=0, help="download and play voicemail id")
    args = parser.parse_args(argv)

    try:
        opener = _mac_api_opener(args)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    base = args.pi_url.rstrip("/")
    try:
        inbox = _get_json(opener, base, "/api/inbox")
        if args.json:
            print(json.dumps(inbox, indent=2))
        else:
            print(_format_inbox(inbox, base=base, limit=args.limit))
        if args.play_vm:
            _play_voicemail(opener, base, args.play_vm, inbox)
    except Exception as e:
        print(f"mac-inbox: {e}", file=sys.stderr)
        return 1
    return 0


def _add_console_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--pi-url",
        default=os.environ.get("OPERATOR_PI_URL", "http://operator.local:8788"),
        help="Pi console URL, e.g. http://operator.local:8788",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("OPERATOR_DESKTOP_TOKEN", ""),
        help="shared OPERATOR_DESKTOP_TOKEN (preferred for inbox/status)",
    )
    parser.add_argument(
        "--console-password",
        default=os.environ.get("OPERATOR_CONSOLE_PASSWORD", ""),
        help="shared OPERATOR_CONSOLE_PASSWORD (fallback if no desktop token)",
    )


def _mac_api_opener(args: argparse.Namespace) -> urllib.request.OpenerDirector:
    token = str(getattr(args, "token", "") or "").strip()
    if token:
        return _desktop_token_opener(token)
    password = str(getattr(args, "console_password", "") or "").strip()
    if password:
        return _console_login(str(args.pi_url).rstrip("/"), password)
    raise ValueError(
        "OPERATOR_DESKTOP_TOKEN or OPERATOR_CONSOLE_PASSWORD is required."
    )


class _DesktopAuthHandler(urllib.request.BaseHandler):
    def __init__(self, token: str) -> None:
        self._token = token

    def http_request(self, req: urllib.request.Request) -> urllib.request.Request:
        req.add_header("Authorization", f"Bearer {self._token}")
        return req

    https_request = http_request  # type: ignore[assignment]


def _desktop_token_opener(token: str) -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(_DesktopAuthHandler(token))


def _console_login(base: str, password: str) -> urllib.request.OpenerDirector:
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    body = json.dumps({"password": password}).encode()
    req = urllib.request.Request(
        base + "/api/login",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with opener.open(req, timeout=10) as resp:
            json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:240]
        raise RuntimeError(f"HTTP {e.code}: {detail}") from e
    return opener


def _get_json(
    opener: urllib.request.OpenerDirector,
    base: str,
    path: str,
) -> dict[str, Any]:
    req = urllib.request.Request(base + path, method="GET")
    try:
        with opener.open(req, timeout=10) as resp:
            data = json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:240]
        raise RuntimeError(f"HTTP {e.code}: {detail}") from e
    return data if isinstance(data, dict) else {}


def _get_bytes(opener: urllib.request.OpenerDirector, url: str) -> bytes:
    try:
        with opener.open(urllib.request.Request(url, method="GET"), timeout=20) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:240]
        raise RuntimeError(f"HTTP {e.code}: {detail}") from e


def _format_status(status: dict[str, Any]) -> str:
    readiness = status.get("readiness") if isinstance(status.get("readiness"), dict) else {}
    clients = status.get("desktop_clients") if isinstance(status.get("desktop_clients"), list) else []
    lines = [
        "Operator status",
        f"  state: {status.get('state') or 'unknown'}",
        f"  readiness: {readiness.get('level') or 'unknown'}",
        f"  last digit: {status.get('last_digit') if status.get('last_digit') is not None else '-'}",
        f"  desktop clients: {len(clients)}",
    ]
    for client in clients:
        if not isinstance(client, dict):
            continue
        caps = ",".join(client.get("capabilities") or [])
        online = "online" if client.get("online") else "offline"
        lines.append(f"    {client.get('client_id')}: {online} {caps}".rstrip())
    return "\n".join(lines)


def _format_inbox(payload: dict[str, Any], *, base: str, limit: int = 10) -> str:
    sms = payload.get("sms") if isinstance(payload.get("sms"), list) else []
    voicemails = (
        payload.get("voicemails") if isinstance(payload.get("voicemails"), list) else []
    )
    limit = max(1, int(limit))
    lines = [
        f"Inbox: {int(payload.get('waiting') or 0)} waiting",
        "SMS",
    ]
    if not sms:
        lines.append("  none")
    for item in sms[:limit]:
        if not isinstance(item, dict):
            continue
        lines.append(_format_sms(item))

    lines.append("Voicemail")
    if not voicemails:
        lines.append("  none")
    for item in voicemails[:limit]:
        if not isinstance(item, dict):
            continue
        lines.append(_format_voicemail(item, base=base))
    return "\n".join(lines)


def _format_sms(item: dict[str, Any]) -> str:
    direction = str(item.get("direction") or "")
    if direction == "out":
        who = item.get("to_name") or item.get("to_e164") or "unknown"
        prefix = "OUT"
        party = f"to {who}"
    else:
        who = item.get("from_name") or item.get("from_e164") or "unknown"
        prefix = "NEW" if item.get("heard_at") is None else "IN"
        party = f"from {who}"
    body = _compact(str(item.get("body") or ""), 96)
    return f"  #{item.get('id')} {prefix} {_format_time(item.get('created_at'))} {party}: {body}"


def _format_voicemail(item: dict[str, Any], *, base: str) -> str:
    who = item.get("from_name") or item.get("from_e164") or "unknown"
    prefix = "NEW" if item.get("heard_at") is None else "OLD"
    dur = _format_duration(item.get("duration_s"))
    audio_url = _absolute_url(base, str(item.get("audio_url") or ""))
    return (
        f"  #{item.get('id')} {prefix} {_format_time(item.get('created_at'))} "
        f"from {who} ({dur}) {audio_url}"
    )


def _play_voicemail(
    opener: urllib.request.OpenerDirector,
    base: str,
    voicemail_id: int,
    inbox: dict[str, Any],
) -> None:
    voicemails = inbox.get("voicemails") if isinstance(inbox.get("voicemails"), list) else []
    vm = next(
        (
            v
            for v in voicemails
            if isinstance(v, dict) and int(v.get("id") or 0) == voicemail_id
        ),
        None,
    )
    if vm is None:
        raise RuntimeError(f"voicemail {voicemail_id} not found in inbox")
    url = _absolute_url(base, str(vm.get("audio_url") or ""))
    if sys.platform != "darwin":
        print(f"mac-inbox: voicemail audio: {url}")
        return
    data = _get_bytes(opener, url)
    path = Path(tempfile.gettempdir()) / f"operator-voicemail-{voicemail_id}.wav"
    path.write_bytes(data)
    subprocess.run(["open", str(path)], check=True)
    print(f"mac-inbox: opened voicemail {voicemail_id}: {path}")


def _absolute_url(base: str, path: str) -> str:
    return urljoin(base.rstrip("/") + "/", path)


def _compact(value: str, limit: int) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _format_time(value: Any) -> str:
    try:
        return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return "-"


def _format_duration(value: Any) -> str:
    try:
        seconds = max(0, int(round(float(value))))
    except (TypeError, ValueError):
        seconds = 0
    mins, secs = divmod(seconds, 60)
    return f"{mins}:{secs:02d}"


def _register(base: str, token: str, client_id: str, name: str) -> None:
    payload = {
        "client_id": client_id,
        "name": name,
        "capabilities": CAPABILITIES,
    }
    data = _post_json(base, "/api/desktop/register", token, payload)
    client = data.get("client") or {}
    print(
        "mac-client: registered "
        f"{client.get('client_id') or client_id} at {base} "
        f"caps={','.join(client.get('capabilities') or CAPABILITIES)}",
        flush=True,
    )


def _event_loop(
    base: str,
    token: str,
    client_id: str,
    *,
    verbose: bool = False,
    notify_mode: str = "notification",
) -> None:
    qs = urlencode({"client_id": client_id})
    req = urllib.request.Request(
        f"{base}/api/desktop/events?{qs}",
        method="GET",
        headers={
            "Accept": "text/event-stream",
            "Authorization": f"Bearer {token}",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        print("mac-client: listening", flush=True)
        event = ""
        data_lines: list[str] = []
        last_keepalive_log = 0.0
        while True:
            raw = resp.readline()
            if raw == b"":
                raise RuntimeError("SSE stream ended")
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                _handle_sse(base, token, client_id, event, data_lines, notify_mode=notify_mode)
                event = ""
                data_lines = []
                continue
            if line.startswith(":"):
                if verbose and time.monotonic() - last_keepalive_log >= 55.0:
                    print(f"mac-client: keepalive from {base} as {client_id}", flush=True)
                    last_keepalive_log = time.monotonic()
                continue
            if line.startswith("event:"):
                event = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].lstrip())


def _handle_sse(
    base: str,
    token: str,
    client_id: str,
    event: str,
    data_lines: list[str],
    *,
    notify_mode: str = "notification",
) -> None:
    if not data_lines:
        return
    try:
        payload = json.loads("\n".join(data_lines))
    except json.JSONDecodeError:
        return
    if event == "ready":
        print("mac-client: stream ready", flush=True)
        return
    if event != "command":
        return
    command_id = str(payload.get("id") or "")
    status = "ok"
    message = ""
    try:
        message = _execute_command(payload, notify_mode=notify_mode)
    except Exception as e:
        status = "error"
        message = str(e)
        print(f"mac-client: command failed: {message}", file=sys.stderr)
    _ack(base, token, client_id, command_id, status, message)


def _execute_command(command: dict[str, Any], *, notify_mode: str = "notification") -> str:
    kind = str(command.get("type") or "")
    payload = command.get("payload") if isinstance(command.get("payload"), dict) else {}
    if kind == "desktop.open_url":
        url = validate_open_url(str(payload.get("url") or ""))
        _open_url(url)
        print(f"mac-client: opened {url}", flush=True)
        return url
    if kind == "desktop.notify":
        title = str(payload.get("title") or "Operator")
        body = str(payload.get("body") or "")
        _notify(title, body, mode=notify_mode)
        summary = _notification_summary(title, body)
        print(f"mac-client: notified ({notify_mode}): {summary}", flush=True)
        return summary
    raise ValueError(f"unsupported command: {kind}")


def _open_url(url: str) -> None:
    if sys.platform != "darwin":
        raise RuntimeError("open_url is only implemented for macOS")
    subprocess.run(["open", url], check=True)


def _notify(title: str, body: str, *, mode: str = "notification") -> None:
    if sys.platform != "darwin":
        return
    mode = _notify_mode(mode)
    if mode in ("notification", "both"):
        script = (
            "display notification "
            f"{_applescript_string(body[:240])} "
            "with title "
            f"{_applescript_string((title or 'Operator')[:80])}"
        )
        subprocess.run(["osascript", "-e", script], check=True)
    if mode in ("alert", "both"):
        script = (
            "display alert "
            f"{_applescript_string((title or 'Operator')[:80])} "
            "message "
            f"{_applescript_string(body[:240])} "
            "as informational giving up after 8"
        )
        subprocess.run(["osascript", "-e", script], check=True)


def _notification_summary(title: str, body: str) -> str:
    title = " ".join((title or "Operator").split())[:80]
    body = " ".join((body or "").split())
    if len(body) > 180:
        body = body[:177].rstrip() + "..."
    return f"{title}: {body}" if body else title


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _notify_mode(value: str) -> str:
    mode = value.strip().lower()
    return mode if mode in {"notification", "alert", "both"} else "notification"


def _ack(
    base: str,
    token: str,
    client_id: str,
    command_id: str,
    status: str,
    message: str,
) -> None:
    _post_json(
        base,
        "/api/desktop/ack",
        token,
        {
            "client_id": client_id,
            "command_id": command_id,
            "status": status,
            "message": message,
        },
    )


def _post_json(base: str, path: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        base + path,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:240]
        raise RuntimeError(f"HTTP {e.code}: {detail}") from e


def _applescript_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
