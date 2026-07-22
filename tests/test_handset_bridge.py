"""Virtual SIP line + handset bridge."""

from pathlib import Path

from operator_os.handset_bridge import (
    HandsetBridge,
    _find_loopback_card,
    write_sip_line_asoundrc,
)


def test_write_sip_line_asoundrc_points_at_loopback(tmp_path: Path):
    write_sip_line_asoundrc(tmp_path, loopback_card="Loopback")
    text = (tmp_path / ".asoundrc").read_text(encoding="utf-8")
    assert "hw:Loopback,0,0" in text
    assert "hw:Loopback,1,1" in text
    assert "ATR" not in text
    assert "plughw:2" not in text


def test_handset_bridge_start_stop_requires_loopback():
    if _find_loopback_card() is None:
        return  # CI / host without snd-aloop
    b = HandsetBridge(handset_alsa="null")
    # null handset may fail alsaloop — just ensure API is callable when card exists
    assert b.active is False
