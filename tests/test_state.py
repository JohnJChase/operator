"""State machine checks."""

from operator_os.state import Event, PhoneController, State


def test_hangup_from_dial_tone_returns_idle():
    ctl = PhoneController()
    ctl.handle(Event("off_hook"))
    assert ctl.state == State.DIAL_TONE
    ctl.handle(Event("hangup"))
    assert ctl.state == State.ON_HOOK_IDLE


def test_hangup_from_playing_service():
    ctl = PhoneController()
    ctl.handle(Event("off_hook"))
    ctl.handle(Event("digit", value=1))
    assert ctl.state == State.PLAYING_SERVICE
    tr = ctl.handle(Event("hangup"))
    assert ctl.state == State.ON_HOOK_IDLE
    assert "audio_stop" in tr.actions
    assert "ring_stop" in tr.actions


def test_hangup_from_ringing_stops_ring():
    ctl = PhoneController()
    ctl.handle(Event("ring_start"))
    assert ctl.state == State.INCOMING_RINGING
    tr = ctl.handle(Event("on_hook"))
    assert ctl.state == State.ON_HOOK_IDLE
    assert "ring_stop" in tr.actions


def test_pickup_to_digit_to_service():
    ctl = PhoneController()
    ctl.handle(Event("off_hook"))
    ctl.handle(Event("pulse"))
    assert ctl.state == State.COLLECTING_DIGIT
    ctl.handle(Event("digit", value=2))
    assert ctl.state == State.PLAYING_SERVICE
    ctl.handle(Event("service_done"))
    assert ctl.state == State.DIAL_TONE
