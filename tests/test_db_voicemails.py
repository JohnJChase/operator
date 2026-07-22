"""SQLite voicemail store."""

from pathlib import Path

from operator_os import db as store


def test_insert_list_mark_delete_voicemail(tmp_path: Path):
    store.configure(tmp_path / "vm.sqlite3")
    store.init_db()
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF" + b"\x00" * 40)
    row = store.insert_voicemail(from_e164="+15551234567", path=str(wav), duration_s=1.5)
    assert row.id > 0
    assert store.unheard_voicemail_count() == 1
    assert store.list_unheard_voicemails()[0].id == row.id
    store.mark_voicemail_heard(row.id)
    assert store.unheard_voicemail_count() == 0
    assert store.delete_voicemail(row.id) is True
    assert not wav.exists()
    assert store.get_voicemail(row.id) is None
