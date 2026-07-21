"""Local service digit map."""

from operator_os.services import handle_digit


def test_digit_zero_help():
    r = handle_digit(0)
    assert r.kind == "speak"
    assert "Operator" in r.text


def test_digit_one_news_or_fallback():
    r = handle_digit(1)
    assert r.kind in ("speak", "play_file")
    if r.kind == "speak":
        assert "news" in r.text.lower() or "not yet" in r.text.lower()


def test_digit_nine_outside_seize():
    r = handle_digit(9)
    assert r.kind == "outside_seize"
