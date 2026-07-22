"""SMS webhook parsing and tool send path (mocked)."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from operator_os import db as store
from operator_os.local_tools import LocalTools
from operator_os.sms import parse_inbound_webhook


SAMPLE_RECEIVED = {
    "data": {
        "event_type": "message.received",
        "id": "event-1",
        "payload": {
            "id": "msg-abc",
            "direction": "inbound",
            "text": "Hello desk",
            "from": {"phone_number": "+15551234567"},
            "to": [{"phone_number": "+12025550100"}],
        },
    }
}


def test_parse_inbound_webhook():
    parsed = parse_inbound_webhook(SAMPLE_RECEIVED)
    assert parsed is not None
    assert parsed["telnyx_id"] == "msg-abc"
    assert parsed["from_e164"] == "+15551234567"
    assert parsed["body"] == "Hello desk"


def test_parse_ignores_delivery_update():
    payload = {
        "data": {
            "event_type": "message.finalized",
            "payload": {
                "id": "msg-out",
                "direction": "outbound",
                "text": "x",
                "from": {"phone_number": "+12025550100"},
                "to": [{"phone_number": "+15551234567"}],
            },
        }
    }
    assert parse_inbound_webhook(payload) is None


def test_confirm_message_sends_when_configured(tmp_path: Path):
    store.configure(tmp_path / "t.sqlite3")
    store.init_db()
    audio = MagicMock()
    tools = LocalTools(audio=audio, voice_mode=True)
    tools.prepare_message(to="5551234567", text="hi there")
    fake = MagicMock()
    fake.telnyx_id = "out-99"
    fake.to_e164 = "+15551234567"
    fake.from_e164 = "+12025550100"
    fake.body = "hi there"
    with (
        patch("operator_os.sms.sms_configured", return_value=True),
        patch("operator_os.sms.send_sms", return_value=fake) as send,
        patch("operator_os.sms.sms_from", return_value="+12025550100"),
    ):
        out = json.loads(tools.confirm_message(True))
    send.assert_called_once()
    assert out["ok"] is True
    assert out["sent"] is True
    assert store.get_message(1) is not None


def test_confirm_message_refuses_without_provider():
    audio = MagicMock()
    tools = LocalTools(audio=audio, voice_mode=True)
    tools.prepare_message(to="+15551234567", text="hello")
    with patch("operator_os.sms.sms_configured", return_value=False):
        out = json.loads(tools.confirm_message(True))
    assert out["ok"] is False
    assert out.get("sent") is False
