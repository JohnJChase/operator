"""AI operator local tools and client helpers."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from operator_os.ai_operator import LocalTools
from operator_os.openai_client import function_calls, output_text
from operator_os.services import handle_digit


def test_digit_zero_is_operator():
    r = handle_digit(0)
    assert r.kind == "operator"


def test_confirm_outside_requires_prepare():
    audio = MagicMock()
    tools = LocalTools(audio=audio)
    out = json.loads(tools.confirm_outside_line(True))
    assert out["ok"] is False
    audio.seize_outside_line.assert_not_called()


def test_confirm_outside_after_prepare_seizes():
    audio = MagicMock()
    tools = LocalTools(audio=audio)
    tools.prepare_outside_line()
    out = json.loads(tools.confirm_outside_line(True))
    assert out["ok"] is True and out["seized"] is True
    audio.seize_outside_line.assert_called_once()


def test_confirm_message_never_sends_without_provider():
    audio = MagicMock()
    tools = LocalTools(audio=audio)
    tools.prepare_message("Hello from the operator")
    out = json.loads(tools.confirm_message(True))
    assert out.get("sent") is False
    assert "not sent" in out.get("error", "").lower() or out["ok"] is False


def test_output_text_and_function_calls():
    resp = {
        "output": [
            {
                "type": "function_call",
                "name": "get_weather",
                "call_id": "c1",
                "arguments": "{}",
            },
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "Sunny."}],
            },
        ]
    }
    assert function_calls(resp)[0]["name"] == "get_weather"
    assert output_text(resp) == "Sunny."
