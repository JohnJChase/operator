"""Calendar Meet dial-in parsing (no live Google calls)."""

from operator_os.google_calendar import (
    MeetDialIn,
    _dedupe_key,
    _e164_from_tel_uri,
    _self_rsvp,
    dedupe_meetings,
    extract_meet_dial_in,
    resolve_meeting_to_join,
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
            "conferenceId": "abc-defg-hij",
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
    assert dial.conference_id == "abc-defg-hij"


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


def test_self_rsvp_accepted_and_organizer():
    assert (
        _self_rsvp(
            {
                "attendees": [
                    {"email": "a@x.com", "responseStatus": "declined"},
                    {"email": "me@x.com", "self": True, "responseStatus": "accepted"},
                ]
            }
        )
        == "accepted"
    )
    assert _self_rsvp({"organizer": {"self": True}}) == "accepted"


def test_dedupe_same_conference_across_calendars():
    a = MeetDialIn(
        title="Standup",
        e164="+15550100999",
        pin="1",
        conference_id="abc-defg-hij",
        self_rsvp="needsAction",
    )
    b = MeetDialIn(
        title="Standup",
        e164="+15550100999",
        pin="1",
        conference_id="abc-defg-hij",
        self_rsvp="accepted",
    )
    out = dedupe_meetings([a, b])
    assert len(out) == 1
    assert out[0].self_rsvp == "accepted"
    assert _dedupe_key(a) == _dedupe_key(b)


def test_resolve_single_and_accepted(monkeypatch):
    alone = MeetDialIn(title="Solo", e164="+15551111111", pin="1", conference_id="a")
    monkeypatch.setattr(
        "operator_os.google_calendar.find_joinable_meetings", lambda: [alone]
    )
    d = resolve_meeting_to_join()
    assert d.meeting == alone
    assert not d.choices

    a = MeetDialIn(
        title="Yes", e164="+15551111111", pin="1", conference_id="a", self_rsvp="accepted"
    )
    b = MeetDialIn(
        title="Maybe",
        e164="+15552222222",
        pin="2",
        conference_id="b",
        self_rsvp="needsAction",
    )
    monkeypatch.setattr(
        "operator_os.google_calendar.find_joinable_meetings", lambda: [a, b]
    )
    d = resolve_meeting_to_join()
    assert d.meeting == a
    assert not d.choices


def test_resolve_ambiguous_offers_choices(monkeypatch):
    a = MeetDialIn(
        title="One", e164="+15551111111", pin="1", conference_id="a", self_rsvp="accepted"
    )
    b = MeetDialIn(
        title="Two", e164="+15552222222", pin="2", conference_id="b", self_rsvp="accepted"
    )
    monkeypatch.setattr(
        "operator_os.google_calendar.find_joinable_meetings", lambda: [a, b]
    )
    d = resolve_meeting_to_join()
    assert d.meeting is None
    assert d.choices == (a, b)


def test_extract_meet_skips_us_fetch_when_disabled(monkeypatch):
    event = {
        "id": "abc",
        "summary": "Shield",
        "conferenceData": {
            "conferenceId": "bue-ifgu-djs",
            "entryPoints": [
                {
                    "entryPointType": "phone",
                    "uri": "tel:+44-20-3957-1704",
                    "regionCode": "GB",
                    "pin": "973212896",
                },
            ],
        },
    }

    def boom(uri: str) -> str:
        raise AssertionError(f"should not fetch {uri}")

    monkeypatch.setattr("operator_os.google_calendar.fetch_us_meet_number", boom)
    dial = extract_meet_dial_in(event, fetch_us=False)
    assert dial is not None
    assert dial.e164.startswith("+44")


def test_ensure_us_dial_in(monkeypatch):
    dial = MeetDialIn(
        title="Shield",
        e164="+442039571704",
        pin="1",
        conference_id="bue-ifgu-djs",
    )
    monkeypatch.setattr(
        "operator_os.google_calendar.fetch_us_meet_number",
        lambda uri: "+16176754444",
    )
    from operator_os.google_calendar import ensure_us_dial_in

    out = ensure_us_dial_in(dial)
    assert out.e164 == "+16176754444"
