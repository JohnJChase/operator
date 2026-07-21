"""Services digit map tests."""

from operator_os.services import handle_digit


def test_digit_zero_menu():
    r = handle_digit(0)
    assert r.kind == "speak"
    assert "Operator" in r.text


def test_digit_one_news_or_fallback():
    r = handle_digit(1)
    assert r.kind in ("speak", "play_file")
    if r.kind == "speak":
        assert "news" in r.text.lower() or "not yet" in r.text.lower()


def test_digit_three_wamu_stream():
    from operator_os.services import WAMU_PLS, handle_digit

    r = handle_digit(3)
    assert r.kind == "stream"
    assert r.url == WAMU_PLS
    assert "WAMU" in r.text


def test_digit_eight_realtime():
    r = handle_digit(8)
    assert r.kind == "realtime_operator"


def test_digit_nine_outside_seize():
    r = handle_digit(9)
    assert r.kind == "outside_seize"


def test_resolve_wamu_pls():
    from operator_os.audio import resolve_stream_url
    from operator_os.services import WAMU_PLS

    media = resolve_stream_url(WAMU_PLS)
    assert media.startswith("http")
    assert "wamu" in media.lower()
    assert not media.lower().endswith(".pls")
