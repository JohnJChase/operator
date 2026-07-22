"""Universal inbox waiting merge + MWI stutter dial."""

from pathlib import Path
from unittest.mock import patch

from operator_os import db as store


def test_list_waiting_chrono_merges_sms_and_vm(tmp_path: Path):
    store.configure(tmp_path / "inbox.sqlite3")
    store.init_db()
    # Older SMS, then VM, then newer SMS.
    m1, _ = store.upsert_inbound(
        telnyx_id="a",
        from_e164="+15550001",
        to_e164="+12025550100",
        body="first",
    )
    store._connection().execute(
        "UPDATE messages SET created_at = 100 WHERE id = ?", (m1.id,)
    )
    store._connection().commit()
    vm = store.insert_voicemail(from_e164="+15550002", path=str(tmp_path / "v.wav"))
    store._connection().execute(
        "UPDATE voicemails SET created_at = 200 WHERE id = ?", (vm.id,)
    )
    store._connection().commit()
    m2, _ = store.upsert_inbound(
        telnyx_id="b",
        from_e164="+15550003",
        to_e164="+12025550100",
        body="third",
    )
    store._connection().execute(
        "UPDATE messages SET created_at = 300 WHERE id = ?", (m2.id,)
    )
    store._connection().commit()

    assert store.waiting_count() == 3
    items = store.list_waiting_chrono()
    assert [i.kind for i in items] == ["sms", "vm", "sms"]
    assert [i.id for i in items] == [m1.id, vm.id, m2.id]
    assert items[0].body == "first"
    assert items[1].from_e164 == "+15550002"


def test_waiting_count_zero_when_empty(tmp_path: Path):
    store.configure(tmp_path / "empty.sqlite3")
    store.init_db()
    assert store.waiting_count() == 0
    assert store.list_waiting_chrono() == []


def test_play_stutter_dial_uses_stutter_stream():
    from operator_os.audio import AudioConfig, AudioRouter

    cfg = AudioConfig(
        alsa_device="null",
        sample_rate_hz=8000,
        channels=1,
        format="S16_LE",
        piper_voice="hfc_female",
        piper_volume=0.6,
    )
    audio = AudioRouter(cfg)
    audio.set_hook(True)
    with patch.object(audio, "_start_tone_stream") as start:
        audio.play_stutter_dial(2.5, wait=False)
    start.assert_called_once()
    assert start.call_args.kwargs.get("stutter_s") == 2.5
    assert start.call_args.kwargs.get("duration_s") is None


def test_wait_event_consumes_flash():
    import threading

    from operator_os.mailbox import _wait_event

    go = threading.Event()
    cancel = threading.Event()
    go.set()
    assert _wait_event(go, cancel, timeout_s=0.2) is True
    assert not go.is_set()


def test_digit_zero_mentions_waiting_messages(tmp_path: Path):
    store.configure(tmp_path / "menu.sqlite3")
    store.init_db()
    store.upsert_inbound(
        telnyx_id="m",
        from_e164="+15550001",
        to_e164="+12025550100",
        body="x",
    )
    from operator_os.services import handle_digit

    r = handle_digit(0)
    assert "1 waiting message" in r.text
    assert "Dial 5 for messages" in r.text
