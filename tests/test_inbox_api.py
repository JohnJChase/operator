"""Inbox API helpers and desktop-token access to /api/inbox*."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from operator_os import db as store
from operator_os import inbox_api
from operator_os.console_hub import ConsoleHub
from operator_os.console_http import ConsoleHttpServer


def test_build_inbox_payload_lists_sms_and_vm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store.configure(tmp_path / "inbox.sqlite3")
    store.init_db()
    monkeypatch.setattr(inbox_api, "VM_DIR", tmp_path.resolve())

    msg, _ = store.upsert_inbound(
        telnyx_id="m1",
        from_e164="+15551234567",
        to_e164="+12025550100",
        body="hello",
    )
    wav = tmp_path / "v1.wav"
    wav.write_bytes(b"RIFF....WAVEfmt ")
    vm = store.insert_voicemail(from_e164="+15550009999", path=str(wav), duration_s=1.25)

    payload = inbox_api.build_inbox_payload()
    assert payload["waiting"] >= 1
    assert any(s["id"] == msg.id and s["body"] == "hello" for s in payload["sms"])
    assert any(
        v["id"] == vm.id and v["audio_url"] == f"/api/inbox/vm/{vm.id}/audio"
        for v in payload["voicemails"]
    )

    assert inbox_api.mark_sms_heard(msg.id) is True
    assert inbox_api.mark_vm_heard(vm.id) is True
    assert inbox_api.resolve_vm_audio_path(vm.id, vm_dir=tmp_path) == wav.resolve()
    assert inbox_api.delete_sms(msg.id) is True
    assert inbox_api.delete_vm(vm.id) is True
    assert inbox_api.resolve_vm_audio_path(vm.id, vm_dir=tmp_path) is None


def test_reply_sms_requires_confirm(tmp_path: Path):
    store.configure(tmp_path / "reply.sqlite3")
    store.init_db()
    result = inbox_api.reply_sms(confirm=False, text="hi", to="+15551234567")
    assert not result.ok
    assert result.code == 400
    assert result.error == "confirm required"


def test_http_inbox_accepts_desktop_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store.configure(tmp_path / "http.sqlite3")
    store.init_db()
    monkeypatch.setattr(inbox_api, "VM_DIR", tmp_path.resolve())
    monkeypatch.setenv("OPERATOR_DESKTOP_TOKEN", "desk-token")
    monkeypatch.delenv("OPERATOR_CONSOLE_PASSWORD", raising=False)

    msg, _ = store.upsert_inbound(
        telnyx_id="m2",
        from_e164="+15551112222",
        to_e164="+12025550100",
        body="ping",
    )
    wav = tmp_path / "clip.wav"
    wav.write_bytes(b"RIFF....WAVEfmt ")
    vm = store.insert_voicemail(from_e164="+15551112222", path=str(wav), duration_s=0.5)

    hub = ConsoleHub()
    srv = ConsoleHttpServer(hub, host="127.0.0.1", port=0)
    srv.start()
    assert srv._httpd is not None
    base = f"http://127.0.0.1:{srv._httpd.server_address[1]}"
    headers = {"Authorization": "Bearer desk-token"}

    try:
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(base + "/api/inbox", timeout=2)
        assert ei.value.code == 401

        req = urllib.request.Request(base + "/api/inbox", headers=headers)
        data = json.loads(urllib.request.urlopen(req, timeout=2).read())
        assert any(s["id"] == msg.id for s in data["sms"])
        assert any(v["id"] == vm.id for v in data["voicemails"])

        audio_req = urllib.request.Request(
            base + f"/api/inbox/vm/{vm.id}/audio", headers=headers
        )
        audio = urllib.request.urlopen(audio_req, timeout=2).read()
        assert audio.startswith(b"RIFF")

        heard = urllib.request.Request(
            base + "/api/inbox/sms/heard",
            data=json.dumps({"id": msg.id}).encode(),
            method="POST",
            headers={**headers, "Content-Type": "application/json"},
        )
        assert json.loads(urllib.request.urlopen(heard, timeout=2).read())["ok"] is True

        vm_heard = urllib.request.Request(
            base + "/api/inbox/vm/heard",
            data=json.dumps({"id": vm.id}).encode(),
            method="POST",
            headers={**headers, "Content-Type": "application/json"},
        )
        assert json.loads(urllib.request.urlopen(vm_heard, timeout=2).read())["ok"] is True
    finally:
        srv.stop()
