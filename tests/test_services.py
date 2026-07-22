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


def test_digit_four_nws_stream():
    from operator_os.services import NWS_KHB36, handle_digit

    r = handle_digit(4)
    assert r.kind == "stream"
    assert r.url == NWS_KHB36
    assert "weather" in r.text.lower()


def test_digit_seven_join_meeting():
    r = handle_digit(7)
    assert r.kind == "join_meeting"


def test_digit_five_mailbox():
    r = handle_digit(5)
    assert r.kind == "mailbox"


def test_digit_zero_menu_mentions_meeting_and_desk():
    r = handle_digit(0)
    assert "7 to join a meeting" in r.text
    assert "8 for the information desk" in r.text
    assert "5 for voicemail" in r.text


def test_digit_zero_menu_mentions_weather_radio():
    r = handle_digit(0)
    assert "4 for weather radio" in r.text


def test_digit_eight_info_desk():
    r = handle_digit(8)
    assert r.kind == "info_desk"


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
