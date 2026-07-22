"""Calendar Meet dial-in parsing (no live Google calls)."""

from operator_os.google_calendar import (
    _e164_from_tel_uri,
    extract_meet_dial_in,
    us_e164_from_tel_meet_html,
)


def test_tel_uri_with_pin_commas():
    e164, pin = _e164_from_tel_uri("tel:+1-555-123-4567,,,,987654321#")
    assert e164 == "+15551234567"
    assert pin == "987654321"


def test_tel_uri_ten_digit():
    e164, pin = _e164_from_tel_uri("tel:+15551234567")
    assert e164 == "+15551234567"
    assert pin == ""


def test_extract_meet_from_conference_data():
    event = {
        "id": "abc",
        "summary": "Standup",
        "conferenceData": {
            "entryPoints": [
                {"entryPointType": "video", "uri": "https://meet.google.com/abc-defg-hij"},
                {
                    "entryPointType": "phone",
                    "uri": "tel:+1-555-010-0999,,,,123456789#",
                    "regionCode": "US",
                    "pin": "123456789",
                },
            ]
        },
    }
    dial = extract_meet_dial_in(event)
    assert dial is not None
    assert dial.title == "Standup"
    assert dial.e164 == "+15550100999"
    assert dial.pin == "123456789"


def test_extract_meet_prefers_us_over_uk():
    event = {
        "id": "abc",
        "summary": "Shield",
        "conferenceData": {
            "entryPoints": [
                {
                    "entryPointType": "phone",
                    "uri": "tel:+44-20-3957-1704,,,,973212896#",
                    "regionCode": "GB",
                    "pin": "973212896",
                },
                {
                    "entryPointType": "phone",
                    "uri": "tel:+1-555-010-0999,,,,973212896#",
                    "regionCode": "US",
                    "pin": "973212896",
                },
            ]
        },
    }
    dial = extract_meet_dial_in(event)
    assert dial is not None
    assert dial.e164 == "+15550100999"


def test_us_from_tel_meet_html():
    html = (
        '[["+442038733170","United Kingdom","+44 20 3873 3170",0,"GB",1],'
        '["+16176754444","United States","+1 617-675-4444",0,"US",1]]'
    )
    assert us_e164_from_tel_meet_html(html) == "+16176754444"


def test_extract_meet_fetches_us_when_calendar_only_has_gb(monkeypatch):
    event = {
        "id": "abc",
        "summary": "Shield",
        "conferenceData": {
            "conferenceId": "bue-ifgu-djs",
            "entryPoints": [
                {
                    "entryPointType": "more",
                    "uri": "https://tel.meet/bue-ifgu-djs?pin=8179360895157",
                    "pin": "8179360895157",
                },
                {
                    "entryPointType": "phone",
                    "uri": "tel:+44-20-3957-1704",
                    "regionCode": "GB",
                    "pin": "973212896",
                },
            ],
        },
    }

    def fake_fetch(uri: str) -> str:
        assert "bue-ifgu-djs" in uri
        return "+16176754444"

    monkeypatch.setattr(
        "operator_os.google_calendar.fetch_us_meet_number", fake_fetch
    )
    dial = extract_meet_dial_in(event)
    assert dial is not None
    assert dial.e164 == "+16176754444"
    assert dial.pin == "8179360895157"


def test_extract_meet_missing_phone():
    assert extract_meet_dial_in({"summary": "No phone", "conferenceData": {}}) is None
