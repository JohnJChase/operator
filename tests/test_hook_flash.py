"""Hook flash vs hangup classification."""

from operator_os.phone import HookFlashClassifier
from operator_os.state import Event, PhoneController, State


def _clf() -> HookFlashClassifier:
    # Matches config defaults: flash 100–700 ms, hangup ≥ 1000 ms.
    return HookFlashClassifier(flash_min_s=0.1, flash_max_s=0.7, hangup_min_s=1.0)


def test_short_dip_is_flash_not_hangup():
    c = _clf()
    assert c.feed(False, now=0.0) == []
    assert c.feed(True, now=0.3) == ["hook_flash"]
    assert c.poll(on_hook=False, now=0.3) == []


def test_long_cradle_down_is_hangup():
    c = _clf()
    assert c.feed(False, now=0.0) == []
    assert c.poll(on_hook=True, now=0.5) == []
    assert c.poll(on_hook=True, now=1.0) == ["on_hook"]
    # Second poll does not repeat hangup.
    assert c.poll(on_hook=True, now=1.2) == []


def test_double_flash_within_window():
    c = _clf()
    c.feed(False, now=0.0)
    assert c.feed(True, now=0.25) == ["hook_flash"]
    c.feed(False, now=0.40)
    assert c.feed(True, now=0.65) == ["hook_flash_2"]


def test_bounce_below_flash_min_ignored():
    c = _clf()
    c.feed(False, now=0.0)
    assert c.feed(True, now=0.05) == []


def test_fsm_flash_is_noop_but_accepted():
    ctl = PhoneController()
    ctl.handle(Event("off_hook"))
    assert ctl.state == State.DIAL_TONE
    tr = ctl.handle(Event("hook_flash"))
    assert ctl.state == State.DIAL_TONE
    assert tr.reason == "hook_flash"
    tr = ctl.handle(Event("hook_flash_2"))
    assert ctl.state == State.DIAL_TONE
    assert tr.reason == "hook_flash_2"
