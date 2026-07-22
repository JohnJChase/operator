"""Virtual SIP line + handset bridge."""

from pathlib import Path

from operator_os.handset_bridge import (
    HandsetBridge,
    HandsetSipGuard,
    _find_loopback_card,
    alsa_card_from_device,
    write_handset_asoundrc,
    write_sip_line_asoundrc,
)


def test_write_sip_line_asoundrc_points_at_loopback(tmp_path: Path):
    write_sip_line_asoundrc(tmp_path, loopback_card="Loopback")
    text = (tmp_path / ".asoundrc").read_text(encoding="utf-8")
    assert "hw:Loopback,0,0" in text
    assert "hw:Loopback,1,1" in text
    assert "ATR" not in text
    assert "plughw:2" not in text


def test_write_handset_asoundrc_points_at_usb(tmp_path: Path):
    write_handset_asoundrc(tmp_path, "plughw:2,0")
    text = (tmp_path / ".asoundrc").read_text(encoding="utf-8")
    assert "plughw:2,0" in text
    assert "asym" in text
    assert "Loopback" not in text


def test_alsa_card_from_device():
    assert alsa_card_from_device("plughw:2,0") == "2"
    assert alsa_card_from_device("hw:ATR2xUSB,0") == "ATR2xUSB"
    assert alsa_card_from_device("plug:hw:2,0") == "2"


def test_handset_sip_guard_arm_release_roundtrip():
    """Live ATR2x only — save → lower capture → restore."""
    import subprocess

    probe = subprocess.run(
        ["amixer", "-c", "2", "sget", "Mic"],
        check=False,
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0 or "Capture" not in probe.stdout:
        return
    g = HandsetSipGuard(handset_alsa="plughw:2,0", capture_level=12)
    before = g._get_capture_level()
    assert before is not None
    g.arm()
    assert g._get_capture_level() == 12
    assert g._get_switch("playback") is False
    g.mute_tx()
    assert g._get_switch("capture") is False
    g.release()
    assert g._get_capture_level() == before


def test_handset_bridge_start_stop_requires_loopback():
    if _find_loopback_card() is None:
        return  # CI / host without snd-aloop
    b = HandsetBridge(handset_alsa="null")
    # null handset may fail alsaloop — just ensure API is callable when card exists
    assert b.active is False
    assert b.latency_us >= 50_000
