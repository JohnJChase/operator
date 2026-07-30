"""Mac companion client behavior that does not require macOS UI."""

from operator_os.mac_client import _notification_summary


def test_notification_summary_includes_title_and_body():
    assert _notification_summary("Message from Alice", "hello there") == (
        "Message from Alice: hello there"
    )


def test_notification_summary_truncates_body():
    summary = _notification_summary("Operator", "hello " * 80)
    assert summary.startswith("Operator: hello")
    assert len(summary) <= len("Operator: ") + 180
    assert summary.endswith("...")
