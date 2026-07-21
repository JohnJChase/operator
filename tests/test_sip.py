"""SIP helpers and outside-line number normalization."""

from operator_os.sip import normalize_nanp


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
