"""Hook flash vs hangup classification + HOOK_PENDING chart."""

from operator_os.phone import HookFlashClassifier
from operator_os.state import Event, PhoneController, State


def _clf() -> HookFlashClassifier:
    # Matches config defaults: flash 100–700 ms, hangup ≥ 1000 ms.
    return HookFlashClassifier(flash_min_s=0.1, flash_max_s=0.7, hangup_min_s=1.0)


def test_cradle_down_emits_before_discrimination():
    c = _clf()
    assert c.feed(False, now=0.0) == ["cradle_down"]
    assert c.feed(True, now=0.3) == ["hook_flash"]
    assert c.poll(on_hook=False, now=0.3) == []


def test_long_cradle_down_is_hangup():
    c = _clf()
    assert c.feed(False, now=0.0) == ["cradle_down"]
    assert c.poll(on_hook=True, now=0.5) == []
    assert c.poll(on_hook=True, now=1.0) == ["on_hook"]
    assert c.poll(on_hook=True, now=1.2) == []


def test_double_flash_within_window():
    c = _clf()
    c.feed(False, now=0.0)
    assert c.feed(True, now=0.25) == ["hook_flash"]
    c.feed(False, now=0.40)
    assert c.feed(True, now=0.65) == ["hook_flash_2"]


def test_bounce_below_flash_min_is_cradle_bounce():
    c = _clf()
    c.feed(False, now=0.0)
    assert c.feed(True, now=0.05) == ["cradle_bounce"]


def test_hook_pending_flash_resumes_playing_service():
    ctl = PhoneController()
    ctl.handle(Event("off_hook"))
    ctl.handle(Event("digit", value=3))
    assert ctl.state == State.PLAYING_SERVICE
    tr = ctl.handle(Event("cradle_down"))
    assert ctl.state == State.HOOK_PENDING
    assert "audio_stop" in tr.actions
    assert ctl.resume_state == State.PLAYING_SERVICE
    tr = ctl.handle(Event("hook_flash"))
    assert ctl.state == State.PLAYING_SERVICE
    assert tr.reason == "flash_resume"
    assert "resume_service" in tr.actions
    assert ctl.resume_state is None


def test_hook_pending_hangup_to_idle():
    ctl = PhoneController()
    ctl.handle(Event("off_hook"))
    ctl.handle(Event("cradle_down"))
    assert ctl.state == State.HOOK_PENDING
    tr = ctl.handle(Event("on_hook"))
    assert ctl.state == State.ON_HOOK_IDLE
    assert "sip_hangup" in tr.actions


def test_hook_pending_flash_resumes_dial_tone():
    ctl = PhoneController()
    ctl.handle(Event("off_hook"))
    ctl.handle(Event("cradle_down"))
    tr = ctl.handle(Event("hook_flash"))
    assert ctl.state == State.DIAL_TONE
    assert "dial_tone" in tr.actions
