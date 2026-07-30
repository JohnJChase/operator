"""Desktop bridge registry and HTTP auth checks."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

import pytest

from operator_os.console_hub import ConsoleHub
from operator_os.console_http import ConsoleHttpServer
from operator_os.desktop_bridge import (
    DesktopBridge,
    DesktopRegistry,
    meeting_title,
    meet_video_url,
    sms_notification_payload,
    validate_open_url,
)
from operator_os.google_calendar import MeetDialIn


def test_desktop_registry_queues_for_online_capable_client():
    reg = DesktopRegistry()
    client = reg.register("John's MacBook", "John's MacBook", ["open_url"])
    cid = client["client_id"]

    assert not reg.has_online_client(capability="open_url")
    reg.connect(cid)
    assert reg.has_online_client(capability="open_url")

    cmd = reg.queue_command("desktop.open_url", {"url": "https://meet.google.com/abc-defg-hij"})
    assert cmd is not None
    assert reg.next_command(cid, timeout_s=0.01) == cmd

    reg.ack(cid, cmd["id"], "ok")
    assert reg.clients()[0]["last_ack"]["command_id"] == cmd["id"]


def test_desktop_registry_requires_matching_capability():
    reg = DesktopRegistry()
    client = reg.register("display", "Display", ["notify"])
    reg.connect(client["client_id"])

    assert reg.queue_command("desktop.open_url", {"url": "https://example.com"}) is None
    assert reg.queue_command("desktop.notify", {"title": "Operator", "body": "hello"})


def test_validate_open_url():
    assert validate_open_url("https://example.com/a") == "https://example.com/a"
    with pytest.raises(ValueError):
        validate_open_url("file:///tmp/nope")
    with pytest.raises(ValueError):
        validate_open_url("https://user:pw@example.com")


def test_meet_video_url_from_dial_in():
    dial = MeetDialIn(
        title="Standup",
        e164="+15550100999",
        conference_id="abc-defg-hij",
    )
    assert meet_video_url(dial) == "https://meet.google.com/abc-defg-hij"
    assert meeting_title(dial) == "Standup"


def test_desktop_bridge_named_intents():
    bridge = DesktopBridge()
    client = bridge.register_client("macbook", "MacBook", ["open_url", "notify"])
    cid = client["client_id"]
    bridge.connect_client(cid)

    meet = MeetDialIn(
        title="Standup",
        e164="+15550100999",
        conference_id="abc-defg-hij",
    )
    delivery = bridge.open_meeting(meet)
    assert delivery.ok
    cmd = bridge.next_command(cid, timeout_s=0.01)
    assert cmd == delivery.command
    assert cmd["type"] == "desktop.open_url"
    assert cmd["payload"]["url"] == "https://meet.google.com/abc-defg-hij"

    delivery = bridge.notify_inbound_sms(
        message_id=7,
        from_e164="+15551234567",
        from_name="Alice",
        body="hello",
    )
    assert delivery.ok
    cmd = bridge.next_command(cid, timeout_s=0.01)
    assert cmd == delivery.command
    assert cmd["type"] == "desktop.notify"
    assert cmd["payload"]["title"] == "Message from Alice"


def test_desktop_bridge_reports_no_client_for_intent():
    bridge = DesktopBridge()
    delivery = bridge.notify(title="Operator", body="hello")
    assert not delivery.ok
    assert delivery.reason == "no_client"
    assert bridge.client_summary() == "none"


def test_desktop_bridge_client_summary():
    bridge = DesktopBridge()
    client = bridge.register_client("macbook", "MacBook", ["open_url", "notify"])
    assert bridge.client_summary() == "macbook:offline:open_url,notify"
    bridge.connect_client(client["client_id"])
    assert bridge.client_summary() == "macbook:online:open_url,notify"


def test_sms_notification_payload_prefers_contact_name_and_truncates():
    payload = sms_notification_payload(
        message_id=42,
        from_e164="+15551234567",
        from_name="Alice",
        body="hello " * 80,
    )
    assert payload["title"] == "Message from Alice"
    assert payload["message_id"] == 42
    assert payload["from_e164"] == "+15551234567"
    assert len(payload["body"]) <= 220
    assert payload["body"].endswith("...")


def test_http_desktop_register_requires_bearer(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPERATOR_DESKTOP_TOKEN", "desk-token")
    hub = ConsoleHub()
    srv = ConsoleHttpServer(hub, host="127.0.0.1", port=0)
    srv.start()
    assert srv._httpd is not None
    base = f"http://127.0.0.1:{srv._httpd.server_address[1]}"

    payload = json.dumps(
        {
            "client_id": "macbook",
            "name": "MacBook",
            "capabilities": ["open_url", "notify"],
        }
    ).encode()

    def post(token: str = ""):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(
            base + "/api/desktop/register",
            data=payload,
            method="POST",
            headers=headers,
        )
        return urllib.request.urlopen(req, timeout=2)

    try:
        with pytest.raises(urllib.error.HTTPError) as ei:
            post()
        assert ei.value.code == 401

        out = json.loads(post("desk-token").read())
        assert out["ok"] is True
        assert out["client"]["client_id"] == "macbook"
        assert hub.desktop.clients()[0]["capabilities"] == ["open_url", "notify"]
    finally:
        srv.stop()


def test_http_desktop_sse_receives_queued_command(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPERATOR_DESKTOP_TOKEN", "desk-token")
    hub = ConsoleHub()
    hub.register_desktop_client("macbook", "MacBook", ["open_url"])
    srv = ConsoleHttpServer(hub, host="127.0.0.1", port=0)
    srv.start()
    assert srv._httpd is not None
    base = f"http://127.0.0.1:{srv._httpd.server_address[1]}"

    req = urllib.request.Request(
        base + "/api/desktop/events?client_id=macbook",
        method="GET",
        headers={"Authorization": "Bearer desk-token", "Accept": "text/event-stream"},
    )
    resp = urllib.request.urlopen(req, timeout=3)
    try:
        event, data = _read_sse(resp)
        assert event == "ready"
        assert data["ok"] is True

        delivery = hub.request_desktop_open_url(
            url="https://meet.google.com/abc-defg-hij",
        )
        assert delivery.ok
        event, data = _read_sse(resp)
        assert event == "command"
        assert data["id"] == delivery.command["id"]
        assert data["payload"]["url"] == "https://meet.google.com/abc-defg-hij"
    finally:
        resp.close()
        srv.stop()


def _read_sse(resp) -> tuple[str, dict[str, Any]]:
    event = ""
    lines: list[str] = []
    while True:
        raw = resp.readline()
        assert raw, "SSE stream ended"
        line = raw.decode("utf-8").rstrip("\r\n")
        if not line:
            if lines:
                return event, json.loads("\n".join(lines))
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            lines.append(line[len("data:") :].lstrip())
