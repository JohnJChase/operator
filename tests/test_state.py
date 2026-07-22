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


def test_join_meeting_place_call_from_playing_service():
    ctl = PhoneController()
    ctl.handle(Event("off_hook"))
    ctl.handle(Event("digit", value=7))
    assert ctl.state == State.PLAYING_SERVICE
    tr = ctl.handle(Event("place_call"))
    assert ctl.state == State.SIP_CALL
    assert "sip_dial" in tr.actions
    assert tr.reason == "join_meeting"


def test_outside_cancel_returns_dial_tone():
    ctl = PhoneController()
    ctl.handle(Event("off_hook"))
    ctl.handle(Event("digit", value=9))
    tr = ctl.handle(Event("outside_cancel"))
    assert ctl.state == State.DIAL_TONE
    assert "fx_release" in tr.actions


def test_outside_first_pulse_stops_dial_tone():
    ctl = PhoneController()
    ctl.handle(Event("off_hook"))
    ctl.handle(Event("digit", value=9))
    tr = ctl.handle(Event("pulse"))
    assert ctl.state == State.OUTSIDE_LINE
    assert "audio_stop" in tr.actions


def test_inbound_ring_answer_to_sip_call():
    ctl = PhoneController()
    tr = ctl.handle(Event("ring_start"))
    assert ctl.state == State.INCOMING_RINGING
    assert "ring_start" in tr.actions
    tr = ctl.handle(Event("off_hook"))
    assert ctl.state == State.SIP_CALL
    assert "sip_answer" in tr.actions
    assert "ring_stop" in tr.actions


def test_inbound_cancel_returns_idle():
    ctl = PhoneController()
    ctl.handle(Event("ring_start"))
    tr = ctl.handle(Event("incoming_cancel"))
    assert ctl.state == State.ON_HOOK_IDLE
    assert "ring_stop" in tr.actions
    assert "sip_hangup" in tr.actions


def test_inbound_timeout_to_voicemail():
    ctl = PhoneController()
    ctl.handle(Event("ring_start"))
    tr = ctl.handle(Event("voicemail_answer"))
    assert ctl.state == State.VOICEMAIL
    assert "ring_stop" in tr.actions
    assert "sip_answer" in tr.actions
    tr = ctl.handle(Event("vm_done"))
    assert ctl.state == State.ON_HOOK_IDLE
    assert "sip_hangup" in tr.actions


def test_voicemail_intercept_to_sip_call():
    ctl = PhoneController()
    ctl.handle(Event("ring_start"))
    ctl.handle(Event("voicemail_answer"))
    tr = ctl.handle(Event("off_hook"))
    assert ctl.state == State.SIP_CALL
    assert tr.reason == "vm_intercept"


def test_meet_choosing_digit_places_call():
    ctl = PhoneController()
    ctl.handle(Event("off_hook"))
    ctl.handle(Event("digit", value=7))
    assert ctl.state == State.PLAYING_SERVICE
    tr = ctl.handle(Event("meet_choose"))
    assert ctl.state == State.MEET_CHOOSING
    assert "announce_meet_choices" in tr.actions
    tr = ctl.handle(Event("pulse"))
    assert ctl.state == State.MEET_CHOOSING
    tr = ctl.handle(Event("digit", value=1))
    assert ctl.state == State.SIP_CALL
    assert "sip_dial" in tr.actions
    assert tr.reason == "meet_digit_1"


def test_meet_choosing_timeout_to_dial_tone():
    ctl = PhoneController()
    ctl.handle(Event("off_hook"))
    ctl.handle(Event("digit", value=7))
    ctl.handle(Event("meet_choose"))
    tr = ctl.handle(Event("meet_timeout"))
    assert ctl.state == State.DIAL_TONE
    assert "dial_tone" in tr.actions


def test_sms_alerting_pickup_and_miss():
    ctl = PhoneController()
    tr = ctl.handle(Event("sms_alert", value=1))
    assert ctl.state == State.SMS_ALERTING
    assert "ring_sms" in tr.actions
    tr = ctl.handle(Event("off_hook"))
    assert ctl.state == State.PLAYING_SERVICE
    assert "announce_sms" in tr.actions

    ctl = PhoneController()
    ctl.handle(Event("sms_alert", value=2))
    tr = ctl.handle(Event("pickup_timeout"))
    assert ctl.state == State.ON_HOOK_IDLE
    assert tr.reason == "sms_missed"


def test_chart_edges_cover_hook_pending_and_sms():
    from operator_os.state import CHART_EDGES, State

    labels = {(e.source, e.event, e.dest) for e in CHART_EDGES}
    assert (State.DIAL_TONE, "cradle_down", State.HOOK_PENDING) in labels
    assert (State.ON_HOOK_IDLE, "sms_alert", State.SMS_ALERTING) in labels
    assert (State.HOOK_PENDING, "hangup", State.ON_HOOK_IDLE) in labels
