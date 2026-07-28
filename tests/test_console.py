"""Phase A diagnostic console — hub, readiness, auth, chart JSON."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

import pytest

from operator_os.console_hub import (
    ConsoleHub,
    RING_TEST_MS,
    chart_edges_json,
    compute_readiness,
    digit_menu_tree,
)
from operator_os.console_http import ConsoleHttpServer


def test_chart_edges_json_shape():
    data = chart_edges_json()
    assert "states" in data and "edges" in data
    assert "DIAL_TONE" in data["states"]
    assert data["edges"]
    e0 = data["edges"][0]
    assert {"source", "event", "dest", "actions"} <= set(e0.keys())


def test_digit_menu_has_outside_and_meet():
    digits = {d["digit"] for d in digit_menu_tree()}
    assert {0, 1, 3, 7, 9} <= digits


def test_readiness_ready_when_nothing_wanted():
    r = compute_readiness(
        sip_wanted=False,
        sip_registered=False,
        calendar_wanted=False,
        calendar_ok=False,
    )
    assert r["level"] == "READY"
    assert r["reasons"] == []


def test_readiness_degraded_sip_and_calendar():
    r = compute_readiness(
        sip_wanted=True,
        sip_registered=False,
        calendar_wanted=True,
        calendar_ok=False,
    )
    assert r["level"] == "DEGRADED"
    assert "sip_not_registered" in r["reasons"]
    assert "calendar_not_linked" in r["reasons"]


def test_hub_digits_and_outside_buffer():
    hub = ConsoleHub()
    hub.note_digit(9)
    hub.note_digit(5)
    hub.set_outside_buffer("5551212")
    hub.publish({"state": "OUTSIDE_LINE"})
    st = hub.status()
    assert st["last_digit"] == 5
    assert st["last_digits"] == [9, 5]
    assert st["outside_buffer"] == "5551212"
    assert st["state"] == "OUTSIDE_LINE"


def test_ring_test_queue_once():
    hub = ConsoleHub()
    assert hub.request_ring_test() is True
    assert hub.request_ring_test() is False
    assert hub.take_ring_test() is True
    assert hub.take_ring_test() is False
    assert RING_TEST_MS == 1500


def test_login_rejects_empty_password(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OPERATOR_CONSOLE_PASSWORD", raising=False)
    hub = ConsoleHub()
    assert hub.login("") is None
    assert hub.login("x") is None


def test_http_auth_and_status(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPERATOR_CONSOLE_PASSWORD", "test-console-pw")
    hub = ConsoleHub()
    hub.publish({"state": "DIAL_TONE", "readiness": {"level": "READY", "reasons": []}})
    srv = ConsoleHttpServer(hub, host="127.0.0.1", port=0)
    # Bind with ephemeral port: ThreadingHTTPServer gets port 0 → OS assigns.
    srv.port = 0
    srv.start()
    assert srv._httpd is not None
    port = srv._httpd.server_address[1]
    base = f"http://127.0.0.1:{port}"

    jar = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def req(path: str, *, data: bytes | None = None, method: str | None = None):
        r = urllib.request.Request(
            base + path,
            data=data,
            method=method or ("POST" if data is not None else "GET"),
            headers={"Content-Type": "application/json"} if data is not None else {},
        )
        return opener.open(r, timeout=2)

    with pytest.raises(urllib.error.HTTPError) as ei:
        req("/api/status")
    assert ei.value.code == 401

    with pytest.raises(urllib.error.HTTPError) as ei:
        req("/api/login", data=json.dumps({"password": ""}).encode())
    assert ei.value.code == 403

    r = req("/api/login", data=json.dumps({"password": "test-console-pw"}).encode())
    assert json.loads(r.read())["ok"] is True

    st = json.loads(req("/api/status").read())
    assert st["state"] == "DIAL_TONE"
    assert "password" not in json.dumps(st).lower()
    assert "telnyx" not in json.dumps(st).lower()

    chart = json.loads(req("/api/chart").read())
    assert "edges" in chart

    ring = json.loads(req("/api/ring-test", data=b"{}").read())
    assert ring["ok"] is True
    assert ring["ms"] == RING_TEST_MS
    assert hub.take_ring_test() is True

    srv.stop()
