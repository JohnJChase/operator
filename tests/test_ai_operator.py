"""Local dial menu + Realtime voice operator."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from operator_os.local_tools import LocalTools
from operator_os.services import LOCAL_MENU, handle_digit


def test_digit_zero_is_local_menu():
    r = handle_digit(0)
    assert r.kind == "speak"
    assert "Dial 1" in r.text
    assert "8" in r.text
    assert r.text == LOCAL_MENU


def test_digit_eight_is_realtime():
    r = handle_digit(8)
    assert r.kind == "realtime_operator"


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


def test_local_intent_time():
    from operator_os.realtime_operator import _local_intent

    assert _local_intent("What time is it?") == "get_current_time"
    assert _local_intent("how's the weather") == "get_weather"
    assert _local_intent("umm hello") is None


def test_announce_from_tool_json():
    from operator_os.realtime_operator import _announce_from_tool_json

    assert _announce_from_tool_json('{"ok":true,"announce":"The time is 1:00 PM."}') == (
        "The time is 1:00 PM."
    )
    assert _announce_from_tool_json('{"ok":true,"spoken":"Hello."}') == "Hello."
    assert _announce_from_tool_json('{"ok":false,"error":"x"}') is None


def test_get_current_time_announce_is_short():
    from unittest.mock import MagicMock

    from operator_os.local_tools import LocalTools

    tools = LocalTools(audio=MagicMock(), voice_mode=True)
    out = json.loads(tools.get_current_time())
    assert out["ok"] is True
    assert "announce" in out
    assert out["announce"].startswith("The time is ")
    assert len(out["announce"].split()) <= 8


def test_pcm_rms_dbfs_silence_and_tone():
    from operator_os.realtime_operator import pcm_rms_dbfs

    silence = b"\x00\x00" * 800
    assert pcm_rms_dbfs(silence) < -90
    # Loud full-scale square-ish samples
    loud = b"\xff\x7f" * 800
    assert pcm_rms_dbfs(loud) > -3


def test_echo_guarding_is_echo_mode():
    from operator_os.realtime_operator import RealtimeMode, RealtimeSession

    session = RealtimeSession(
        audio=MagicMock(),
        events=MagicMock(),
        tools=MagicMock(),
        api_key="test",
    )
    assert session.echo_guarding is False
    session.mode = RealtimeMode.ECHO
    assert session.echo_guarding is True
    assert session.listening is False
    session.mode = RealtimeMode.LISTEN
    assert session.listening is True


def test_realtime_transition_chart():
    from operator_os.realtime_operator import RealtimeMode, RtEvent, _transition

    # Hangup / hush releases from every mode into ECHO (bleed clear).
    for mode in RealtimeMode:
        tr = _transition(mode, RtEvent("hush"))
        assert tr.mode == RealtimeMode.ECHO
        assert "interrupt" in tr.actions

    # Wrong signal: response_done while talking does not reopen listen.
    tr = _transition(RealtimeMode.SPEAK, RtEvent("response_done", value={}))
    assert tr.mode == RealtimeMode.SPEAK
    assert "open_mic" not in tr.actions
    assert "fx_release" not in tr.actions
    assert "resolve_tools" not in tr.actions

    # Trunk announce from thinking: plant seize then speak.
    tr = _transition(RealtimeMode.THINKING, RtEvent("trunk_announce", value="The time is 1 PM."))
    assert tr.mode == RealtimeMode.SPEAK
    assert tr.actions == ("fx_seize", "speak")

    # Greet throws the switch onto the operator jack, then she speaks.
    tr = _transition(RealtimeMode.LISTEN, RtEvent("greet", value="Operator."))
    assert tr.mode == RealtimeMode.SPEAK
    assert tr.actions == ("fx_seize", "speak")

    # speak_done → echo; echo_elapsed → plant release + open mic.
    tr = _transition(RealtimeMode.SPEAK, RtEvent("speak_done"))
    assert tr.mode == RealtimeMode.ECHO
    tr = _transition(RealtimeMode.ECHO, RtEvent("echo_elapsed"))
    assert tr.mode == RealtimeMode.LISTEN
    assert tr.actions == ("fx_release", "open_mic")

    # No trunk seized — quiet return to listen (no fx_release).
    tr = _transition(RealtimeMode.THINKING, RtEvent("no_answer"))
    assert tr.mode == RealtimeMode.LISTEN
    assert tr.actions == ("open_mic",)

    # Empty transcript does not request tools.
    tr = _transition(RealtimeMode.THINKING, RtEvent("transcript", value=""))
    assert tr.mode == RealtimeMode.LISTEN
    assert tr.actions == ("open_mic",)
    assert "request_tools" not in tr.actions

    # Time transcript arms fulfill (local intent), not an immediate listen reopen.
    tr = _transition(RealtimeMode.THINKING, RtEvent("transcript", value="What time is it?"))
    assert tr.mode == RealtimeMode.THINKING
    assert tr.actions == ("fulfill_intent",)


def test_autotune_suggestions():
    from operator_os.realtime_autotune import (
        suggest_capture_gain,
        suggest_echo_guard_ms,
        suggest_gate,
        suggest_playback_gain,
        suggest_vad_threshold,
    )

    assert suggest_gate(-48.0) == -42.0
    assert suggest_gate(-23.0) == -17.0  # above floor, not capped below it
    assert suggest_gate(-5.0) == -12.0  # ceiling
    assert suggest_playback_gain(-30.0, -42.0, 0.5) < 0.5
    assert suggest_echo_guard_ms(-30.0, -42.0, 900, open_fraction=0.2) > 900
    assert suggest_vad_threshold(2, False, 0.75) > 0.75
    assert suggest_vad_threshold(0, True, 0.75) < 0.75


def test_voice_mode_play_returns_announce(monkeypatch, tmp_path):
    audio = MagicMock()
    tools = LocalTools(audio=audio, voice_mode=True)
    cache = tmp_path / "weather.json"
    cache.write_text('{"spoken": "Seventy degrees and fair."}\n', encoding="utf-8")
    monkeypatch.setattr("operator_os.local_tools.WEATHER_CACHE", cache)
    out = json.loads(tools.play_weather())
    assert out["ok"] is True
    assert "Seventy" in out["announce"]
    audio.play_file.assert_not_called()
    audio.speak.assert_not_called()
