"""SIP helpers and outside-line number normalization."""

from operator_os.sip import (
    SipCredentials,
    _pjsua_media_args,
    _telnyx_reject_message,
    normalize_nanp,
    speak_phone_number,
)


def test_media_args_use_default_sound_devices():
    """Hard-coded PortAudio 0 kills idle inbound on Pi (HDMI / no capture)."""
    args = _pjsua_media_args(null_audio=False)
    assert not any(a.startswith("--capture-dev=") for a in args)
    assert not any(a.startswith("--playback-dev=") for a in args)
    assert "--snd-auto-close=0" in args
    null = _pjsua_media_args(null_audio=True)
    assert "--null-audio" in null
    assert not any(a.startswith("--capture-dev=") for a in null)


def test_normalize_ten_digit_nanp():
    assert normalize_nanp("7035551212") == "+17035551212"


def test_normalize_eleven_digit():
    assert normalize_nanp("17035551212") == "+17035551212"


def test_normalize_e164():
    assert normalize_nanp("+17035551212") == "+17035551212"


def test_normalize_rejects_short():
    assert normalize_nanp("911") is None
    assert normalize_nanp("5551212") is None
    assert normalize_nanp("") is None


def test_speak_phone_number_nanp_digit_words():
    # +1 202-306-1203
    assert speak_phone_number("+12023061203") == (
        "two zero two, three zero six, one two zero three"
    )
    assert "billion" not in speak_phone_number("+15551234567").lower()


def test_local_id_prefers_caller_id():
    creds = SipCredentials(
        username="userx",
        password="secret",
        caller_id="+12025551212",
    )
    assert creds.local_id() == "sip:+12025551212@sip.telnyx.com"


def test_local_id_falls_back_to_username():
    creds = SipCredentials(username="userx", password="secret")
    assert creds.local_id() == "sip:userx@sip.telnyx.com"


def test_telnyx_reject_non_verified():
    msg = _telnyx_reject_message(
        "SIP/2.0 403 Can not make calls to non-verified numbers at this account level D60"
    )
    assert msg is not None
    assert "verified" in msg.lower()


def test_inbound_poll_detects_incoming_call_phrase():
    """pjsua console says 'Incoming call for account', not always 'INCOMING'."""
    from operator_os.sip import SipInboundListener

    class _FakePj:
        def read_log(self) -> str:
            return ">>> Incoming call for account 2!\nPress a to answer\n"

    lis = SipInboundListener(
        credentials=SipCredentials(username="u", password="p", caller_id="+12025550100")
    )
    lis._phase = "listen"
    lis._mark = 0
    lis._pj = _FakePj()  # type: ignore[assignment]
    assert lis.poll() == "incoming"
    assert lis._phase == "ringing"


def test_cli_from_sip_log_finds_e164():
    from operator_os.sip import _cli_from_sip_log

    text = "Incoming call\nFrom: <sip:+12025551212@sip.telnyx.com>"
    assert _cli_from_sip_log(text) == "+12025551212"


def test_greeting_duration_reads_wav(tmp_path):
    """OGM wait must track the file on disk, not a hardcoded sleep."""
    import array
    import wave

    from operator_os.sip import SipCredentials, SipInboundListener, wav_duration_s

    rate = 8000
    n = rate * 2  # 2.0 seconds
    pcm = array.array("h", [0] * n)
    path = tmp_path / "greeting.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    assert abs(wav_duration_s(path) - 2.0) < 0.01
    lis = SipInboundListener(
        credentials=SipCredentials(username="u", password="p", caller_id="+12025550100"),
        greeting_wav=path,
    )
    assert abs(lis._greeting_duration_s() - 2.0) < 0.01


def test_parse_conf_ports_roles():
    from operator_os.sip import _parse_conf_ports

    text = """
Conference ports:
Port #0[8KHz/1] Master/sound
Port #1[8KHz/1] greeting.wav
Port #2[8KHz/1] _active.wav
Port #3[8KHz/1] sip:+15551212@sip.telnyx.com
"""
    ports = _parse_conf_ports(text)
    assert ports["sound"] == 0
    assert ports["file"] == 1
    assert ports["recorder"] == 2
    assert ports["call"] == 3


def test_outbound_remote_ended_on_disconnected():
    from operator_os.sip import SipCallSession

    class _FakePj:
        alive = True
        log = "CONFIRMED\n"

        def is_alive(self) -> bool:
            return self.alive

        def read_log(self) -> str:
            return self.log

    sess = SipCallSession(
        e164="+16176754444",
        credentials=SipCredentials(username="u", password="p", caller_id="+12025550100"),
    )
    pj = _FakePj()
    sess._pj = pj  # type: ignore[assignment]
    sess._log_mark = len(pj.log)
    assert sess.remote_ended() is False
    pj.log += "state changed to DISCONNECTED\n"
    assert sess.remote_ended() is True
