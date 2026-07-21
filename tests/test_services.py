"""Local service digit map."""

from operator_os.services import handle_digit


def test_digit_zero_help():
    r = handle_digit(0)
    assert r.kind == "speak"
    assert "Operator" in r.text


def test_digit_one_missing_cache():
    r = handle_digit(1)
    assert r.kind == "speak"
    assert "not yet" in r.text.lower()


def test_digit_nine_outside_unavailable():
    r = handle_digit(9)
    assert r.kind == "effect_then_speak"
    assert "not yet available" in r.text.lower()
