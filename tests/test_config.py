"""Config loading."""

from pathlib import Path

from operator_os.config import load_profile


def test_load_rev_a_profile():
    p = load_profile(Path("config/hardware_profile.yaml"))
    assert p.gpio.hook_bcm == 17
    assert p.gpio.dial_pulse_bcm == 10
    assert p.gpio.ring_bcm == 22
    assert p.dial.digit_done_ms == 700
    assert p.audio.alsa_device == "plughw:2,0"
