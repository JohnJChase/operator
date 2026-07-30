"""Mac companion client behavior that does not require macOS UI."""

from pathlib import Path

from operator_os.mac_client import (
    _absolute_url,
    _format_inbox,
    _format_status,
    _notification_summary,
    _notify_mode,
    _truthy,
    run_mac_inbox,
)


def test_notification_summary_includes_title_and_body():
    assert _notification_summary("Message from Alice", "hello there") == (
        "Message from Alice: hello there"
    )


def test_notification_summary_truncates_body():
    summary = _notification_summary("Operator", "hello " * 80)
    assert summary.startswith("Operator: hello")
    assert len(summary) <= len("Operator: ") + 180
    assert summary.endswith("...")


def test_truthy():
    assert _truthy("1")
    assert _truthy("true")
    assert _truthy("YES")
    assert not _truthy("")
    assert not _truthy("0")


def test_notify_mode_defaults_to_notification():
    assert _notify_mode("") == "notification"
    assert _notify_mode("nonsense") == "notification"
    assert _notify_mode("alert") == "alert"
    assert _notify_mode("BOTH") == "both"


def test_absolute_url_handles_api_path():
    assert _absolute_url("http://operator.local:8788", "/api/inbox/vm/3/audio") == (
        "http://operator.local:8788/api/inbox/vm/3/audio"
    )


def test_format_inbox_lists_sms_and_voicemail():
    text = _format_inbox(
        {
            "waiting": 2,
            "sms": [
                {
                    "id": 7,
                    "direction": "in",
                    "from_name": "Alice",
                    "body": "hello from the other desk",
                    "created_at": None,
                    "heard_at": None,
                }
            ],
            "voicemails": [
                {
                    "id": 3,
                    "from_e164": "+15551234567",
                    "duration_s": 65,
                    "created_at": None,
                    "heard_at": None,
                    "audio_url": "/api/inbox/vm/3/audio",
                }
            ],
        },
        base="http://operator.local:8788",
    )

    assert "Inbox: 2 waiting" in text
    assert "#7 NEW - from Alice: hello from the other desk" in text
    assert "#3 NEW - from +15551234567 (1:05)" in text
    assert "http://operator.local:8788/api/inbox/vm/3/audio" in text


def test_format_status_lists_desktop_clients():
    text = _format_status(
        {
            "state": "DIAL_TONE",
            "readiness": {"level": "READY"},
            "last_digit": 7,
            "desktop_clients": [
                {"client_id": "john-macbook", "online": True, "capabilities": ["notify"]}
            ],
        }
    )

    assert "state: DIAL_TONE" in text
    assert "readiness: READY" in text
    assert "john-macbook: online notify" in text


def test_run_mac_inbox_requires_auth(monkeypatch, capsys):
    monkeypatch.delenv("OPERATOR_CONSOLE_PASSWORD", raising=False)
    monkeypatch.delenv("OPERATOR_DESKTOP_TOKEN", raising=False)

    assert run_mac_inbox(["--pi-url", "http://operator.local:8788"]) == 2
    err = capsys.readouterr().err
    assert "OPERATOR_DESKTOP_TOKEN or OPERATOR_CONSOLE_PASSWORD is required" in err


def test_run_mac_inbox_fetches_with_desktop_token(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    from operator_os import db as store
    from operator_os.console_hub import ConsoleHub
    from operator_os.console_http import ConsoleHttpServer

    monkeypatch.setenv("OPERATOR_DESKTOP_TOKEN", "desk-token")
    monkeypatch.delenv("OPERATOR_CONSOLE_PASSWORD", raising=False)
    store.configure(tmp_path / "operator.sqlite3")
    store.init_db()
    store.upsert_inbound(
        telnyx_id="sms-1",
        from_e164="+15551234567",
        to_e164="+12025550100",
        body="real inbox smoke",
    )
    srv = ConsoleHttpServer(ConsoleHub(), host="127.0.0.1", port=0)
    srv.start()
    assert srv._httpd is not None
    port = srv._httpd.server_address[1]

    try:
        assert run_mac_inbox(["--pi-url", f"http://127.0.0.1:{port}"]) == 0
    finally:
        srv.stop()
        store.configure()

    assert "real inbox smoke" in capsys.readouterr().out


def test_run_mac_inbox_fetches_console_inbox(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    from operator_os import db as store
    from operator_os.console_hub import ConsoleHub
    from operator_os.console_http import ConsoleHttpServer

    monkeypatch.setenv("OPERATOR_CONSOLE_PASSWORD", "pw")
    monkeypatch.delenv("OPERATOR_DESKTOP_TOKEN", raising=False)
    store.configure(tmp_path / "operator-console.sqlite3")
    store.init_db()
    store.upsert_inbound(
        telnyx_id="sms-2",
        from_e164="+15551234567",
        to_e164="+12025550100",
        body="console password path",
    )
    srv = ConsoleHttpServer(ConsoleHub(), host="127.0.0.1", port=0)
    srv.start()
    assert srv._httpd is not None
    port = srv._httpd.server_address[1]

    try:
        assert run_mac_inbox(["--pi-url", f"http://127.0.0.1:{port}"]) == 0
    finally:
        srv.stop()
        store.configure()

    assert "console password path" in capsys.readouterr().out


def test_run_mac_call_requires_auth(monkeypatch, capsys):
    monkeypatch.delenv("OPERATOR_CONSOLE_PASSWORD", raising=False)
    monkeypatch.delenv("OPERATOR_DESKTOP_TOKEN", raising=False)

    from operator_os.mac_client import run_mac_call

    assert run_mac_call(["+15551234567"]) == 2
    assert "OPERATOR_DESKTOP_TOKEN or OPERATOR_CONSOLE_PASSWORD is required" in (
        capsys.readouterr().err
    )


def test_run_mac_call_posts_place_call(tmp_path: Path, monkeypatch, capsys):
    from operator_os.console_hub import ConsoleHub
    from operator_os.console_http import ConsoleHttpServer
    from operator_os.mac_client import run_mac_call

    monkeypatch.setenv("OPERATOR_DESKTOP_TOKEN", "desk-token")
    monkeypatch.delenv("OPERATOR_CONSOLE_PASSWORD", raising=False)
    hub = ConsoleHub()
    srv = ConsoleHttpServer(hub, host="127.0.0.1", port=0)
    srv.start()
    assert srv._httpd is not None
    port = srv._httpd.server_address[1]
    try:
        assert run_mac_call(["--pi-url", f"http://127.0.0.1:{port}", "+15551234567"]) == 0
    finally:
        srv.stop()
    assert hub.take_place_call() == "+15551234567"
    assert "requested +15551234567" in capsys.readouterr().out
