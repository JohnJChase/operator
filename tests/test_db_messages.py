"""SQLite messages store."""

from pathlib import Path

from operator_os import db as store


def test_upsert_inbound_idempotent(tmp_path: Path):
    store.configure(tmp_path / "t.sqlite3")
    store.init_db()
    a, created = store.upsert_inbound(
        telnyx_id="msg-1",
        from_e164="+15551212",
        to_e164="+12025550100",
        body="hello",
    )
    assert created is True
    b, created2 = store.upsert_inbound(
        telnyx_id="msg-1",
        from_e164="+15551212",
        to_e164="+12025550100",
        body="hello again",
    )
    assert created2 is False
    assert a.id == b.id
    assert store.unheard_count() == 1


def test_mark_heard_and_list(tmp_path: Path):
    store.configure(tmp_path / "t.sqlite3")
    store.init_db()
    m, _ = store.upsert_inbound(
        telnyx_id="msg-2",
        from_e164="+15559999",
        to_e164="+12025550100",
        body="ping",
    )
    assert store.list_unheard()[0].id == m.id
    store.mark_heard(m.id)
    assert store.unheard_count() == 0
    assert store.list_unheard() == []


def test_insert_outbound(tmp_path: Path):
    store.configure(tmp_path / "t.sqlite3")
    store.init_db()
    out = store.insert_outbound(
        to_e164="+15551212",
        from_e164="+12025550100",
        body="yo",
        telnyx_id="out-1",
    )
    assert out.direction == "out"
    assert out.heard_at is not None
    assert store.unheard_count() == 0
