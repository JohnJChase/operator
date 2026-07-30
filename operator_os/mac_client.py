"""Mac companion client for the Operator desktop bridge."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlencode

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
    parser.add_argument("--once", action="store_true", help="connect once; do not retry")
    args = parser.parse_args(argv)

    if not args.token.strip():
        print("OPERATOR_DESKTOP_TOKEN is required.", file=sys.stderr)
        return 2
    base = args.pi_url.rstrip("/")
    while True:
        try:
            _register(base, args.token, args.client_id, args.name)
            _event_loop(base, args.token, args.client_id)
        except KeyboardInterrupt:
            print("mac-client: quit")
            return 0
        except Exception as e:
            print(f"mac-client: disconnected: {e}", file=sys.stderr)
            if args.once:
                return 1
            time.sleep(2.0)


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


def _event_loop(base: str, token: str, client_id: str) -> None:
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
        while True:
            raw = resp.readline()
            if raw == b"":
                raise RuntimeError("SSE stream ended")
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                _handle_sse(base, token, client_id, event, data_lines)
                event = ""
                data_lines = []
                continue
            if line.startswith(":"):
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
        message = _execute_command(payload)
    except Exception as e:
        status = "error"
        message = str(e)
        print(f"mac-client: command failed: {message}", file=sys.stderr)
    _ack(base, token, client_id, command_id, status, message)


def _execute_command(command: dict[str, Any]) -> str:
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
        _notify(title, body)
        summary = _notification_summary(title, body)
        print(f"mac-client: notified: {summary}", flush=True)
        return summary
    raise ValueError(f"unsupported command: {kind}")


def _open_url(url: str) -> None:
    if sys.platform != "darwin":
        raise RuntimeError("open_url is only implemented for macOS")
    subprocess.run(["open", url], check=True)


def _notify(title: str, body: str) -> None:
    if sys.platform != "darwin":
        return
    script = (
        "display notification "
        f"{_applescript_string(body[:240])} "
        "with title "
        f"{_applescript_string((title or 'Operator')[:80])}"
    )
    subprocess.run(["osascript", "-e", script], check=True)


def _notification_summary(title: str, body: str) -> str:
    title = " ".join((title or "Operator").split())[:80]
    body = " ".join((body or "").split())
    if len(body) > 180:
        body = body[:177].rstrip() + "..."
    return f"{title}: {body}" if body else title


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
