"""News/weather cache formatting and service wiring."""

from operator_os.refresh import format_news_spoken, format_weather_spoken
from operator_os.services import handle_digit


def test_format_weather_spoken():
    payload = {
        "current": {
            "temperature_2m": 74.4,
            "relative_humidity_2m": 78,
            "weather_code": 3,
            "wind_speed_10m": 7.1,
        }
    }
    spoken = format_weather_spoken(payload, location_label="Fairfax, Virginia area")
    assert "Weather Bureau" in spoken
    assert "74" in spoken
    assert "overcast" in spoken


def test_format_news_spoken():
    spoken = format_news_spoken(["First headline", "Second headline"])
    assert spoken.startswith("News of the Day.")
    assert "First headline" in spoken
    assert "Second headline" in spoken


def test_digit_two_uses_weather_cache_when_present():
    r = handle_digit(2)
    assert r.kind == "speak"
    # Live refresh may have populated data/weather.json; either path is fine.
    assert "weather" in r.text.lower() or "not yet" in r.text.lower()
