"""Desktop bridge registry and HTTP auth checks."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from typing import Any

import pytest

from operator_os.console_hub import ConsoleHub
from operator_os.console_http import ConsoleHttpServer
from operator_os.desktop_bridge import (
    DesktopBridge,
    DesktopRegistry,
    DesktopStreamSuperseded,
    meeting_title,
    meet_video_url,
    sms_notification_payload,
    validate_open_url,
)
from operator_os.google_calendar import MeetDialIn
from operator_os.route_priority import INTENT_OPEN_MEETING, WE302_MEET_ID


@pytest.fixture
def priority_file(tmp_path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "route_priority.json"
    monkeypatch.setattr("operator_os.route_priority.PRIORITY_PATH", path)
    monkeypatch.delenv("OPERATOR_DESKTOP_CLIENT_ID", raising=False)
    monkeypatch.delenv("OPERATOR_ROUTE_OPEN_MEETING", raising=False)
    monkeypatch.setenv("OPERATOR_MEET_JOIN_TARGET", "auto")
    return path


def _ack_accept(bridge: DesktopBridge, client_id: str) -> threading.Thread:
    def run() -> None:
        cmd = bridge.next_command(client_id, timeout_s=1.0)
        if cmd:
            bridge.ack_command(client_id, cmd["id"], "accept")

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


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


def test_desktop_bridge_named_intents(priority_file, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPERATOR_MEET_JOIN_TARGET", "desktop")
    bridge = DesktopBridge()
    client = bridge.register_client("macbook", "MacBook", ["open_url", "notify"])
    cid = client["client_id"]
    bridge.connect_client(cid)

    meet = MeetDialIn(
        title="Standup",
        e164="+15550100999",
        conference_id="abc-defg-hij",
    )
    waiter = _ack_accept(bridge, cid)
    delivery = bridge.open_meeting(meet, mode="desktop", accept_timeout=1.0)
    waiter.join(timeout=2.0)
    assert delivery.ok
    assert delivery.handler == cid
    assert delivery.command is not None
    assert delivery.command["type"] == "desktop.open_url"
    assert delivery.command["payload"]["url"] == "https://meet.google.com/abc-defg-hij"

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


def test_desktop_bridge_client_summary(priority_file):
    bridge = DesktopBridge()
    client = bridge.register_client("macbook", "MacBook", ["open_url", "notify"])
    assert bridge.client_summary() == "macbook:offline:open_url,notify:desktop"
    bridge.connect_client(client["client_id"])
    assert bridge.client_summary() == "macbook:online:open_url,notify:desktop"


def test_stale_sse_disconnect_does_not_offline_newer_stream():
    """Mac relaunch: old SSE finally-disconnect must not kill the new stream."""
    reg = DesktopRegistry()
    client = reg.register("macbook", "MacBook", ["open_url", "notify"])
    cid = client["client_id"]
    _info1, gen1 = reg.connect(cid)
    _info2, gen2 = reg.connect(cid)
    assert gen2 != gen1
    assert reg.has_online_client(capability="notify")

    reg.disconnect(cid, generation=gen1)
    assert reg.has_online_client(capability="notify")

    delivery = DesktopBridge(registry=reg).notify(title="Operator", body="hi")
    assert delivery.ok

    reg.disconnect(cid, generation=gen2)
    assert not reg.has_online_client(capability="notify")


def test_superseded_sse_waiter_does_not_steal_commands():
    reg = DesktopRegistry()
    client = reg.register("macbook", "MacBook", ["notify"])
    cid = client["client_id"]
    _, gen1 = reg.connect(cid)
    _, gen2 = reg.connect(cid)

    with pytest.raises(DesktopStreamSuperseded):
        reg.next_command(cid, timeout_s=0.3, generation=gen1)

    bridge = DesktopBridge(registry=reg)
    delivery = bridge.notify(title="Operator", body="hi")
    assert delivery.ok
    cmd = reg.next_command(cid, timeout_s=0.2, generation=gen2)
    assert cmd == delivery.command


def test_notify_fans_out_ignoring_preferred_client(monkeypatch: pytest.MonkeyPatch, priority_file):
    monkeypatch.setenv("OPERATOR_DESKTOP_CLIENT_ID", "mac-a")
    bridge = DesktopBridge()
    for cid in ("mac-b", "mac-a"):
        bridge.register_client(cid, cid, ["open_url", "notify"])
        bridge.connect_client(cid)

    delivery = bridge.notify_inbound_sms(
        message_id=1,
        from_e164="+15551234567",
        from_name="Alice",
        body="hello",
    )
    assert delivery.ok
    cmd_a = bridge.next_command("mac-a", timeout_s=0.01)
    cmd_b = bridge.next_command("mac-b", timeout_s=0.01)
    assert cmd_a is not None and cmd_b is not None
    assert cmd_a["type"] == "desktop.notify"
    assert cmd_b["type"] == "desktop.notify"
    assert cmd_a["payload"]["title"] == "Message from Alice"
    assert cmd_a["id"] != cmd_b["id"]
    assert bridge.next_command("mac-a", timeout_s=0.01) is None


def test_open_url_unicast_to_preferred_when_online(monkeypatch: pytest.MonkeyPatch, priority_file):
    monkeypatch.setenv("OPERATOR_DESKTOP_CLIENT_ID", "mac-b")
    bridge = DesktopBridge()
    for cid in ("mac-a", "mac-b"):
        bridge.register_client(cid, cid, ["open_url", "notify"])
        bridge.connect_client(cid)

    delivery = bridge.open_url(url="https://meet.google.com/abc-defg-hij")
    assert delivery.ok
    assert bridge.next_command("mac-b", timeout_s=0.01) == delivery.command
    assert bridge.next_command("mac-a", timeout_s=0.01) is None


def test_open_url_falls_back_to_first_online_when_preferred_offline(
    monkeypatch: pytest.MonkeyPatch,
    priority_file,
):
    monkeypatch.setenv("OPERATOR_DESKTOP_CLIENT_ID", "mac-offline")
    bridge = DesktopBridge()
    bridge.register_client("mac-offline", "Offline", ["open_url"])
    # registered but not connected
    for cid in ("mac-z", "mac-a"):
        bridge.register_client(cid, cid, ["open_url"])
        bridge.connect_client(cid)

    delivery = bridge.open_url(url="https://example.com/meet")
    assert delivery.ok
    # Stable fallback: lowest client_id among online capable.
    assert bridge.next_command("mac-a", timeout_s=0.01) == delivery.command
    assert bridge.next_command("mac-z", timeout_s=0.01) is None
    assert bridge.next_command("mac-offline", timeout_s=0.01) is None


def test_open_meeting_failover_priority_order(priority_file, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPERATOR_MEET_JOIN_TARGET", "desktop")
    bridge = DesktopBridge()
    bridge.set_priorities({INTENT_OPEN_MEETING: ["mac-b", "mac-a"]})
    for cid in ("mac-a", "mac-b"):
        bridge.register_client(cid, cid, ["open_url", "notify"])
        bridge.connect_client(cid)

    meet = MeetDialIn(
        title="Standup",
        e164="+15550100999",
        conference_id="abc-defg-hij",
    )

    def reject_then_accept() -> None:
        cmd = bridge.next_command("mac-b", timeout_s=1.0)
        assert cmd is not None
        bridge.ack_command("mac-b", cmd["id"], "reject", "busy")
        cmd = bridge.next_command("mac-a", timeout_s=1.0)
        assert cmd is not None
        bridge.ack_command("mac-a", cmd["id"], "accept")

    t = threading.Thread(target=reject_then_accept, daemon=True)
    t.start()
    delivery = bridge.open_meeting(meet, mode="desktop", accept_timeout=1.0)
    t.join(timeout=3.0)
    assert delivery.ok
    assert delivery.handler == "mac-a"


def test_open_meeting_falls_to_we302(priority_file, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPERATOR_MEET_JOIN_TARGET", "auto")
    bridge = DesktopBridge()
    bridge.register_local(WE302_MEET_ID, "WE302 Meet", ["open.meeting"], eligible=lambda: True)
    bridge.set_priorities({INTENT_OPEN_MEETING: ["mac-a", WE302_MEET_ID]})
    bridge.register_client("mac-a", "Mac", ["open_url"])
    bridge.connect_client("mac-a")

    meet = MeetDialIn(
        title="Standup",
        e164="+15550100999",
        conference_id="abc-defg-hij",
    )

    def reject_mac() -> None:
        cmd = bridge.next_command("mac-a", timeout_s=1.0)
        assert cmd is not None
        bridge.ack_command("mac-a", cmd["id"], "reject")

    t = threading.Thread(target=reject_mac, daemon=True)
    t.start()
    delivery = bridge.open_meeting(meet, mode="auto", accept_timeout=1.0)
    t.join(timeout=2.0)
    assert delivery.ok
    assert delivery.handler == WE302_MEET_ID
    assert delivery.command is not None
    assert delivery.command["type"] == "local.meet_sip"


def test_open_meeting_phone_mode_skips_desktop(priority_file):
    bridge = DesktopBridge()
    bridge.register_local(WE302_MEET_ID, "WE302 Meet", ["open.meeting"], eligible=lambda: True)
    bridge.set_priorities({INTENT_OPEN_MEETING: ["mac-a", WE302_MEET_ID]})
    bridge.register_client("mac-a", "Mac", ["open_url"])
    bridge.connect_client("mac-a")

    meet = MeetDialIn(
        title="Standup",
        e164="+15550100999",
        conference_id="abc-defg-hij",
    )
    delivery = bridge.open_meeting(meet, mode="phone", accept_timeout=0.2)
    assert delivery.ok
    assert delivery.handler == WE302_MEET_ID
    assert bridge.next_command("mac-a", timeout_s=0.01) is None


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


def test_http_desktop_register_requires_bearer(monkeypatch: pytest.MonkeyPatch, priority_file):
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


def test_http_routing_get_put(monkeypatch: pytest.MonkeyPatch, priority_file):
    monkeypatch.setenv("OPERATOR_DESKTOP_TOKEN", "desk-token")
    hub = ConsoleHub()
    hub.register_local_station(WE302_MEET_ID, "WE302 Meet", ["open.meeting"], eligible=lambda: True)
    hub.register_desktop_client("macbook", "MacBook", ["open_url"])
    srv = ConsoleHttpServer(hub, host="127.0.0.1", port=0)
    srv.start()
    assert srv._httpd is not None
    base = f"http://127.0.0.1:{srv._httpd.server_address[1]}"

    def call(method: str, path: str, body: dict | None = None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            base + path,
            data=data,
            method=method,
            headers={
                "Authorization": "Bearer desk-token",
                "Content-Type": "application/json",
            },
        )
        return json.loads(urllib.request.urlopen(req, timeout=2).read())

    try:
        snap = call("GET", "/api/routing")
        assert "priorities" in snap
        assert any(s["client_id"] == WE302_MEET_ID for s in snap["stations"])
        saved = call(
            "POST",
            "/api/routing",
            {"priorities": {INTENT_OPEN_MEETING: [WE302_MEET_ID, "macbook"]}},
        )
        assert saved["ok"] is True
        assert saved["priorities"][INTENT_OPEN_MEETING] == [WE302_MEET_ID, "macbook"]
        assert hub.routing_snapshot()["priorities"][INTENT_OPEN_MEETING] == [
            WE302_MEET_ID,
            "macbook",
        ]
    finally:
        srv.stop()


def test_http_desktop_sse_receives_queued_command(monkeypatch: pytest.MonkeyPatch, priority_file):
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
