"""Info desk + local tool checks (Realtime removed)."""

import json
from unittest.mock import MagicMock

from operator_os.local_tools import LocalTools
from operator_os.services import handle_digit


def test_digit_eight_is_info_desk():
    r = handle_digit(8)
    assert r.kind == "info_desk"


def test_confirm_outside_requires_prepare():
    audio = MagicMock()
    tools = LocalTools(audio=audio, voice_mode=True)
    out = json.loads(tools.confirm_outside_line(True))
    assert out["ok"] is False


def test_confirm_outside_after_prepare_seizes():
    audio = MagicMock()
    tools = LocalTools(audio=audio, voice_mode=True)
    tools.prepare_outside_line()
    out = json.loads(tools.confirm_outside_line(True))
    assert out["ok"] is True and out["seized"] is True
    audio.seize_outside_line.assert_called_once()


def test_confirm_message_never_sends_without_provider():
    audio = MagicMock()
    tools = LocalTools(audio=audio, voice_mode=True)
    tools.prepare_message(to="+15551234567", text="hello")
    from unittest.mock import patch

    with patch("operator_os.sms.sms_configured", return_value=False):
        out = json.loads(tools.confirm_message(True))
    assert out["ok"] is False
    assert "provider" in out.get("error", "").lower() or out.get("sent") is False
