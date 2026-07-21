"""SIP helpers and outside-line number normalization."""

from operator_os.sip import SipCredentials, _telnyx_reject_message, normalize_nanp


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
