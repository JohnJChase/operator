"""Phase B — phonebook, streams map, outside resolve."""

from __future__ import annotations

from pathlib import Path

import pytest

from operator_os import db as store
from operator_os.phonebook import (
    lookup_by_short_code,
    resolve_outside_digits,
    speak_from,
    upsert_contact,
)
from operator_os.services import handle_digit
from operator_os.streams import DEFAULT_STREAMS, load_streams, save_streams


@pytest.fixture()
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store.configure(tmp_path / "t.sqlite3")
    store.init_db()
    yield
    store.configure(None)


def test_phonebook_short_code_resolve(tmp_db):
    upsert_contact(name="Desk", e164="2025551212", short_code="11")
    assert resolve_outside_digits("11") == "+12025551212"
    assert lookup_by_short_code("11").name == "Desk"
    # Full NANP still works
    assert resolve_outside_digits("2025559999") == "+12025559999"


def test_speak_from_uses_name(tmp_db):
    upsert_contact(name="Alice", e164="+12025550100")
    assert speak_from("+12025550100") == "Alice"
    assert "two" in speak_from("+12025550999")  # unknown → digit words


def test_streams_save_and_handle_digit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "streams.yaml"
    monkeypatch.setattr("operator_os.streams.STREAMS_PATH", path)
    out = save_streams(
        {
            "3": {"label": "Test Radio", "url": "https://example.com/a.mp3"},
            "4": {"label": "Test NWS", "url": "https://example.com/b.mp3"},
        },
        probe=False,
    )
    assert out["3"]["label"] == "Test Radio"
    loaded = load_streams()
    assert loaded["3"]["url"] == "https://example.com/a.mp3"
    r = handle_digit(3)
    assert r.kind == "stream"
    assert r.url == "https://example.com/a.mp3"
    assert r.text == "Test Radio"


def test_streams_default_without_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("operator_os.streams.STREAMS_PATH", tmp_path / "missing.yaml")
    s = load_streams()
    assert s["3"]["url"] == DEFAULT_STREAMS["3"]["url"]


def test_streams_rejects_bad_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("operator_os.streams.STREAMS_PATH", tmp_path / "s.yaml")
    with pytest.raises(ValueError):
        save_streams(
            {"3": {"label": "x", "url": "ftp://nope"}, "4": DEFAULT_STREAMS["4"]},
            probe=False,
        )


def test_streams_probe_blocks_unreachable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "streams.yaml"
    monkeypatch.setattr("operator_os.streams.STREAMS_PATH", path)

    def boom(url, *, timeout=8.0):
        raise ValueError("unreachable media: timed out")

    monkeypatch.setattr("operator_os.streams.probe_stream_url", boom)
    with pytest.raises(ValueError, match="digit 3"):
        save_streams(
            {
                "3": {"label": "Bad", "url": "https://example.com/dead.mp3"},
                "4": DEFAULT_STREAMS["4"],
            }
        )
    assert not path.is_file()


def test_probe_stream_url_ok(monkeypatch: pytest.MonkeyPatch):
    from operator_os.streams import probe_stream_url

    class FakeResp:
        status = 206

        def read(self, n=-1):
            return b"ID3xxxx"

        def getcode(self):
            return 206

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        "operator_os.audio.resolve_stream_url",
        lambda u, timeout=8.0: "https://cdn.example/live.mp3",
    )
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *a, **k: FakeResp(),
    )
    assert probe_stream_url("https://example.com/x.pls") == "https://cdn.example/live.mp3"


def test_list_messages_and_delete(tmp_db):
    m, _ = store.upsert_inbound(
        telnyx_id="t1", from_e164="+15551112222", to_e164="+12025550100", body="hi"
    )
    assert store.list_messages(limit=5)[0].id == m.id
    assert store.delete_message(m.id) is True
    assert store.get_message(m.id) is None


def test_place_call_queue():
    from operator_os.console_hub import ConsoleHub

    hub = ConsoleHub()
    assert hub.request_place_call("+12025551212") is True
    assert hub.request_place_call("+12025559999") is False
    assert hub.take_place_call() == "+12025551212"
    assert hub.take_place_call() is None
