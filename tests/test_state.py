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
    assert "sip_hangup" in tr.actions


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
    tr = ctl.handle(Event("digit", value=2))
    assert ctl.state == State.PLAYING_SERVICE
    assert "fx_seize" in tr.actions
    assert "play_service" in tr.actions
    tr = ctl.handle(Event("service_done"))
    assert ctl.state == State.DIAL_TONE
    assert "fx_release" in tr.actions
    assert "dial_tone" in tr.actions


def test_digit_from_dial_tone_seizes_plant():
    ctl = PhoneController()
    ctl.handle(Event("off_hook"))
    tr = ctl.handle(Event("digit", value=1))
    assert ctl.state == State.PLAYING_SERVICE
    assert tr.actions == ("audio_stop", "fx_seize", "play_service")


def test_digit_nine_seizes_outside_line():
    ctl = PhoneController()
    ctl.handle(Event("off_hook"))
    tr = ctl.handle(Event("digit", value=9))
    assert ctl.state == State.OUTSIDE_LINE
    assert "fx_outside" in tr.actions
    assert "play_service" not in tr.actions


def test_outside_place_call_and_hangup():
    ctl = PhoneController()
    ctl.handle(Event("off_hook"))
    ctl.handle(Event("digit", value=9))
    tr = ctl.handle(Event("place_call"))
    assert ctl.state == State.SIP_CALL
    assert "sip_dial" in tr.actions
    tr = ctl.handle(Event("hangup"))
    assert ctl.state == State.ON_HOOK_IDLE
    assert "sip_hangup" in tr.actions


def test_outside_cancel_returns_dial_tone():
    ctl = PhoneController()
    ctl.handle(Event("off_hook"))
    ctl.handle(Event("digit", value=9))
    tr = ctl.handle(Event("outside_cancel"))
    assert ctl.state == State.DIAL_TONE
    assert "fx_release" in tr.actions
