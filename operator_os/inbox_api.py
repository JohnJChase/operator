"""Shared inbox/voicemail JSON helpers for console and desktop clients."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
VM_DIR = (ROOT / "data" / "voicemail").resolve()


def build_inbox_payload(*, sms_limit: int = 40, vm_limit: int = 40) -> dict[str, Any]:
    from operator_os import db as store
    from operator_os.phonebook import display_name

    store.init_db()
    sms = []
    for m in store.list_messages(limit=sms_limit):
        sms.append(
            {
                "id": m.id,
                "direction": m.direction,
                "from_e164": m.from_e164,
                "to_e164": m.to_e164,
                "from_name": display_name(m.from_e164) if m.direction == "in" else None,
                "to_name": display_name(m.to_e164) if m.direction == "out" else None,
                "body": m.body,
                "created_at": m.created_at,
                "heard_at": m.heard_at,
                "status": m.status,
            }
        )
    vms = []
    for vm in store.list_voicemails(limit=vm_limit):
        vms.append(
            {
                "id": vm.id,
                "from_e164": vm.from_e164,
                "from_name": display_name(vm.from_e164),
                "created_at": vm.created_at,
                "heard_at": vm.heard_at,
                "duration_s": vm.duration_s,
                "status": vm.status,
                "audio_url": f"/api/inbox/vm/{vm.id}/audio",
            }
        )
    return {
        "sms": sms,
        "voicemails": vms,
        "waiting": store.waiting_count(),
    }


def mark_sms_heard(message_id: int) -> bool:
    from operator_os import db as store

    return store.mark_heard(int(message_id)) is not None


def delete_sms(message_id: int) -> bool:
    from operator_os import db as store

    return store.delete_message(int(message_id))


def mark_vm_heard(voicemail_id: int) -> bool:
    from operator_os import db as store

    return store.mark_voicemail_heard(int(voicemail_id)) is not None


def delete_vm(voicemail_id: int) -> bool:
    from operator_os import db as store

    return store.delete_voicemail(int(voicemail_id))


def resolve_vm_audio_path(
    voicemail_id: int, *, vm_dir: Path | None = None
) -> Path | None:
    from operator_os import db as store

    vm = store.get_voicemail(int(voicemail_id))
    if vm is None:
        return None
    base = (vm_dir or VM_DIR).resolve()
    path = Path(vm.path).resolve()
    if not str(path).startswith(str(base)) or not path.is_file():
        return None
    return path


@dataclass(frozen=True)
class SmsReplyResult:
    ok: bool
    error: str = ""
    code: int = 200
    to: str = ""


def reply_sms(
    *,
    confirm: bool,
    text: str,
    to: str = "",
    message_id: int | None = None,
) -> SmsReplyResult:
    from operator_os import db as store
    from operator_os.sip import normalize_nanp
    from operator_os.sms import send_sms, sms_configured, sms_from

    if not confirm:
        return SmsReplyResult(False, error="confirm required", code=400)
    if not sms_configured():
        return SmsReplyResult(False, error="sms not configured", code=503)
    dest_raw = (to or "").strip()
    if message_id is not None and not dest_raw:
        msg = store.get_message(int(message_id))
        if msg is None:
            return SmsReplyResult(False, error="message not found", code=404)
        dest_raw = msg.from_e164
    dest = normalize_nanp(dest_raw)
    if not dest:
        return SmsReplyResult(False, error="invalid to", code=400)
    body = (text or "").strip()
    if not body or len(body) > 500:
        return SmsReplyResult(False, error="text required (1–500 chars)", code=400)
    try:
        sent = send_sms(to=dest, text=body)
    except Exception as e:
        return SmsReplyResult(False, error=str(e), code=502)
    store.insert_outbound(
        to_e164=sent.to_e164,
        from_e164=sent.from_e164 or sms_from() or "",
        body=sent.body,
        telnyx_id=sent.telnyx_id,
    )
    return SmsReplyResult(True, to=dest)
