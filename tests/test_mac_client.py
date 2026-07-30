"""Mac companion client behavior that does not require macOS UI."""

from operator_os.mac_client import _notification_summary, _notify_mode, _truthy


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
