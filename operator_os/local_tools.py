"""Local phone tools for the Realtime operator. Pi executes; model never touches GPIO/ALSA."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from operator_os.audio import AudioRouter
from operator_os.config import HardwareProfile
from operator_os.services import (
    LOCAL_MENU,
    NEWS_AUDIO_CANDIDATES,
    NEWS_JSON,
    WEATHER_AUDIO,
    WEATHER_CACHE,
    _first_existing,
    _json_spoken,
)

_EMPTY_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}


def _fn(name: str, description: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": parameters or _EMPTY_PARAMS,
    }


TOOL_DEFS: list[dict[str, Any]] = [
    _fn("get_current_time", "REQUIRED for any time/clock question. Returns announce with the local time."),
    _fn("get_weather", "REQUIRED for weather questions. Returns announce with the weather text."),
    _fn("get_news", "REQUIRED for news questions. Returns announce with the news text."),
    _fn("get_device_status", "REQUIRED for status/health questions. Returns a short announce."),
    _fn(
        "play_weather",
        "Play or announce the cached weather report on the handset.",
    ),
    _fn("play_news", "Play or announce the cached news report on the handset."),
    _fn(
        "refresh_weather",
        "Fetch a fresh weather report from Open-Meteo and update the cache.",
    ),
    _fn(
        "refresh_news",
        "Fetch fresh headlines from the news provider and update the cache.",
    ),
    _fn("speak_local_menu", "Recite the local dial-menu options for the caller."),
    _fn(
        "prepare_outside_line",
        "Draft an outside-line seize. Requires confirm_outside_line next.",
    ),
    _fn(
        "confirm_outside_line",
        "Confirm or cancel a prepared outside-line seize.",
        {
            "type": "object",
            "properties": {"confirmed": {"type": "boolean"}},
            "required": ["confirmed"],
            "additionalProperties": False,
        },
    ),
    _fn(
        "prepare_message",
        "Draft an SMS to an E.164 number. Requires confirm_message before send.",
        {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "Destination phone number (10-digit US or +E.164).",
                },
                "text": {"type": "string", "description": "Message body."},
            },
            "required": ["to", "text"],
            "additionalProperties": False,
        },
    ),
    _fn(
        "confirm_message",
        "Confirm or cancel a prepared SMS draft. Confirm sends via Telnyx.",
        {
            "type": "object",
            "properties": {"confirmed": {"type": "boolean"}},
            "required": ["confirmed"],
            "additionalProperties": False,
        },
    ),
    _fn(
        "list_messages",
        "List unheard inbound SMS (ids and from numbers only; no full bodies).",
    ),
    _fn(
        "read_message",
        "Read one inbound SMS aloud by id and mark it heard.",
        {
            "type": "object",
            "properties": {"message_id": {"type": "integer"}},
            "required": ["message_id"],
            "additionalProperties": False,
        },
    ),
    _fn(
        "list_voicemails",
        "List unheard voicemails (ids and from numbers only).",
    ),
    _fn(
        "play_voicemail",
        "Play one voicemail by id on the handset and mark it heard.",
        {
            "type": "object",
            "properties": {"voicemail_id": {"type": "integer"}},
            "required": ["voicemail_id"],
            "additionalProperties": False,
        },
    ),
    _fn(
        "delete_voicemail",
        "Delete one voicemail by id (WAV and DB row).",
        {
            "type": "object",
            "properties": {"voicemail_id": {"type": "integer"}},
            "required": ["voicemail_id"],
            "additionalProperties": False,
        },
    ),
    _fn(
        "callback_voicemail",
        "Announce the callback number for a voicemail (caller dials 9 to place the call).",
        {
            "type": "object",
            "properties": {"voicemail_id": {"type": "integer"}},
            "required": ["voicemail_id"],
            "additionalProperties": False,
        },
    ),
]


@dataclass
class LocalTools:
    """Policy layer the model cannot bypass."""

    audio: AudioRouter
    profile: HardwareProfile | None = None
    status_snapshot: dict[str, Any] = field(default_factory=dict)
    # When True (Realtime duplex), play_* returns text for the model to speak.
    voice_mode: bool = False
    _outside_draft: bool = False
    _message_draft: str | None = None
    _message_to: str | None = None
    line_seized: bool = False

    def dispatch(self, name: str, arguments: dict[str, Any]) -> str:
        fn = {
            "get_weather": self.get_weather,
            "get_news": self.get_news,
            "get_current_time": self.get_current_time,
            "get_device_status": self.get_device_status,
            "play_weather": self.play_weather,
            "play_news": self.play_news,
            "refresh_weather": self.refresh_weather,
            "refresh_news": self.refresh_news,
            "speak_local_menu": self.speak_local_menu,
            "prepare_outside_line": self.prepare_outside_line,
            "confirm_outside_line": self.confirm_outside_line,
            "prepare_message": self.prepare_message,
            "confirm_message": self.confirm_message,
            "list_messages": self.list_messages,
            "read_message": self.read_message,
            "list_voicemails": self.list_voicemails,
            "play_voicemail": self.play_voicemail,
            "delete_voicemail": self.delete_voicemail,
            "callback_voicemail": self.callback_voicemail,
        }.get(name)
        if fn is None:
            return json.dumps({"ok": False, "error": f"unknown tool {name}"})
        return fn(**arguments) if arguments else fn()

    def get_weather(self) -> str:
        text = _json_spoken(WEATHER_CACHE)
        if not text:
            return json.dumps({"ok": False, "error": "weather not on file"})
        return json.dumps({"ok": True, "announce": text, "spoken": text})

    def get_news(self) -> str:
        text = _json_spoken(NEWS_JSON)
        if not text:
            return json.dumps({"ok": False, "error": "news not on file"})
        return json.dumps({"ok": True, "announce": text, "spoken": text})

    def get_current_time(self) -> str:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo("America/New_York"))
        hour = now.strftime("%I").lstrip("0") or "12"
        spoken = f"The time is {hour}:{now.strftime('%M %p')}."
        return json.dumps({"ok": True, "announce": spoken, "spoken": spoken, "iso": now.isoformat()})

    def get_device_status(self) -> str:
        spoken = "Exchange status is on file."
        return json.dumps({"ok": True, "announce": spoken, "status": self.status_snapshot})

    def play_weather(self) -> str:
        if self.voice_mode:
            text = _json_spoken(WEATHER_CACHE)
            if not text:
                return json.dumps({"ok": False, "error": "weather not on file"})
            return json.dumps({"ok": True, "announce": text})
        if WEATHER_AUDIO.is_file() and WEATHER_AUDIO.stat().st_size > 0:
            self.audio.play_file(WEATHER_AUDIO, wait=True)
            return json.dumps({"ok": True, "played": "weather.wav"})
        text = _json_spoken(WEATHER_CACHE)
        if text:
            self.audio.speak(text, wait=True)
            return json.dumps({"ok": True, "spoken": True})
        return json.dumps({"ok": False, "error": "weather not on file"})

    def play_news(self) -> str:
        if self.voice_mode:
            text = _json_spoken(NEWS_JSON)
            if not text:
                return json.dumps({"ok": False, "error": "news not on file"})
            return json.dumps({"ok": True, "announce": text})
        audio = _first_existing(NEWS_AUDIO_CANDIDATES)
        if audio is not None:
            self.audio.play_file(audio, wait=True)
            return json.dumps({"ok": True, "played": audio.name})
        text = _json_spoken(NEWS_JSON)
        if text:
            self.audio.speak(text, wait=True)
            return json.dumps({"ok": True, "spoken": True})
        return json.dumps({"ok": False, "error": "news not on file"})

    def refresh_weather(self) -> str:
        if self.profile is None:
            return json.dumps({"ok": False, "error": "no hardware profile"})
        from operator_os.refresh import load_dotenv, refresh_weather

        load_dotenv()
        try:
            refresh_weather(self.profile, synthesize=not self.voice_mode)
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)[:160]})
        return self.get_weather()

    def refresh_news(self) -> str:
        if self.profile is None:
            return json.dumps({"ok": False, "error": "no hardware profile"})
        from operator_os.refresh import load_dotenv, refresh_news

        load_dotenv()
        try:
            refresh_news(self.profile, synthesize=not self.voice_mode)
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)[:160]})
        return self.get_news()

    def speak_local_menu(self) -> str:
        if self.voice_mode:
            return json.dumps({"ok": True, "announce": LOCAL_MENU})
        self.audio.speak(LOCAL_MENU, wait=True)
        return json.dumps({"ok": True, "spoken": True})

    def prepare_outside_line(self) -> str:
        self._outside_draft = True
        spoken = "Outside line ready. Confirm to connect."
        return json.dumps(
            {
                "ok": True,
                "announce": spoken,
                "draft": "outside_line",
                "next": "confirm_outside_line",
            }
        )

    def confirm_outside_line(self, confirmed: bool) -> str:
        if not self._outside_draft:
            return json.dumps({"ok": False, "error": "no outside-line draft; prepare first"})
        self._outside_draft = False
        if not confirmed:
            return json.dumps({"ok": True, "announce": "Cancelled.", "seized": False})
        # Seize uses AudioRouter tones; ends Realtime usefulness — caller hears trunk.
        self.audio.stop()
        self.audio.seize_outside_line()
        self.line_seized = True
        return json.dumps({"ok": True, "seized": True})

    def prepare_message(self, to: str = "", text: str = "") -> str:
        from operator_os.sip import normalize_nanp

        cleaned = (text or "").strip()
        dest = normalize_nanp(to or "")
        if not dest:
            return json.dumps({"ok": False, "error": "invalid destination number"})
        if not cleaned:
            return json.dumps({"ok": False, "error": "empty message"})
        self._message_draft = cleaned[:500]
        self._message_to = dest
        spoken = f"Message to {dest} drafted. Confirm to send."
        return json.dumps(
            {
                "ok": True,
                "announce": spoken,
                "draft": "message",
                "to": dest,
                "text": self._message_draft,
                "next": "confirm_message",
            }
        )

    def confirm_message(self, confirmed: bool) -> str:
        draft = self._message_draft
        dest = self._message_to
        self._message_draft = None
        self._message_to = None
        if draft is None or dest is None:
            return json.dumps({"ok": False, "error": "no message draft; prepare first"})
        if not confirmed:
            return json.dumps({"ok": True, "announce": "Cancelled.", "sent": False})
        from operator_os import db as store
        from operator_os.sms import send_sms, sms_configured, sms_from

        if not sms_configured():
            return json.dumps(
                {
                    "ok": False,
                    "announce": "Messaging is not configured.",
                    "sent": False,
                    "error": "messaging provider not configured; message not sent",
                }
            )
        try:
            result = send_sms(dest, draft)
            store.insert_outbound(
                to_e164=result.to_e164,
                from_e164=result.from_e164 or sms_from(),
                body=result.body,
                telnyx_id=result.telnyx_id or None,
            )
            return json.dumps(
                {
                    "ok": True,
                    "announce": "Sent.",
                    "sent": True,
                    "to": result.to_e164,
                }
            )
        except Exception as e:
            return json.dumps(
                {
                    "ok": False,
                    "announce": "Unable to send the message.",
                    "sent": False,
                    "error": str(e)[:160],
                }
            )

    def list_messages(self) -> str:
        from operator_os import db as store
        from operator_os.sip import speak_phone_number

        rows = store.list_unheard(limit=10)
        if not rows:
            return json.dumps(
                {"ok": True, "announce": "No unheard messages.", "messages": []}
            )
        parts = []
        summary = []
        for m in rows:
            who = speak_phone_number(m.from_e164) if m.from_e164 else "unknown"
            parts.append(f"number {m.id} from {who}")
            summary.append({"id": m.id, "from": m.from_e164})
        spoken = "Unheard messages: " + "; ".join(parts) + "."
        return json.dumps({"ok": True, "announce": spoken, "messages": summary})

    def read_message(self, message_id: int = 0) -> str:
        from operator_os import db as store
        from operator_os.sip import speak_phone_number

        msg = store.get_message(int(message_id))
        if msg is None or msg.direction != "in":
            return json.dumps({"ok": False, "error": "message not found"})
        store.mark_heard(msg.id)
        who = speak_phone_number(msg.from_e164) if msg.from_e164 else "unknown"
        spoken = f"Message from {who}: {msg.body}"
        return json.dumps(
            {
                "ok": True,
                "announce": spoken,
                "message_id": msg.id,
                "heard": True,
            }
        )

    def list_voicemails(self) -> str:
        from operator_os import db as store
        from operator_os.sip import speak_phone_number

        rows = store.list_unheard_voicemails(limit=10)
        if not rows:
            return json.dumps(
                {"ok": True, "announce": "No new voicemail.", "voicemails": []}
            )
        parts = []
        summary = []
        for vm in rows:
            who = speak_phone_number(vm.from_e164) if vm.from_e164 else "unknown"
            parts.append(f"number {vm.id} from {who}")
            summary.append({"id": vm.id, "from": vm.from_e164})
        spoken = "New voicemail: " + "; ".join(parts) + "."
        return json.dumps({"ok": True, "announce": spoken, "voicemails": summary})

    def play_voicemail(self, voicemail_id: int = 0) -> str:
        from pathlib import Path

        from operator_os import db as store
        from operator_os.sip import speak_phone_number

        vm = store.get_voicemail(int(voicemail_id))
        if vm is None:
            return json.dumps({"ok": False, "error": "voicemail not found"})
        who = speak_phone_number(vm.from_e164) if vm.from_e164 else "unknown"
        path = Path(vm.path)
        if not path.is_file() or path.stat().st_size < 44:
            store.mark_voicemail_heard(vm.id)
            return json.dumps(
                {
                    "ok": False,
                    "error": "recording missing",
                    "announce": f"Voicemail {vm.id} from {who} has no recording.",
                }
            )
        # Always play WAV on the handset (model cannot speak audio).
        self.audio.speak(f"Voicemail from {who}.", wait=True)
        self.audio.play_file(path, wait=True)
        store.mark_voicemail_heard(vm.id)
        return json.dumps(
            {
                "ok": True,
                "announce": f"Played voicemail {vm.id}.",
                "voicemail_id": vm.id,
                "heard": True,
            }
        )

    def delete_voicemail(self, voicemail_id: int = 0) -> str:
        from operator_os import db as store

        ok = store.delete_voicemail(int(voicemail_id))
        if not ok:
            return json.dumps({"ok": False, "error": "voicemail not found"})
        return json.dumps(
            {
                "ok": True,
                "announce": "Deleted.",
                "voicemail_id": int(voicemail_id),
            }
        )

    def callback_voicemail(self, voicemail_id: int = 0) -> str:
        from operator_os import db as store
        from operator_os.sip import speak_phone_number

        vm = store.get_voicemail(int(voicemail_id))
        if vm is None:
            return json.dumps({"ok": False, "error": "voicemail not found"})
        if not vm.from_e164:
            return json.dumps(
                {
                    "ok": False,
                    "error": "no caller id",
                    "announce": "That voicemail has no callback number.",
                }
            )
        who = speak_phone_number(vm.from_e164)
        spoken = (
            f"The number is {who}. Hang up and dial 9, then that number, to call back."
        )
        return json.dumps(
            {
                "ok": True,
                "announce": spoken,
                "voicemail_id": vm.id,
                "from": vm.from_e164,
            }
        )


def build_status_snapshot(profile_name: str) -> dict[str, Any]:
    return {
        "profile": profile_name,
        "weather_cache": WEATHER_CACHE.is_file(),
        "news_cache": NEWS_JSON.is_file(),
        "weather_audio": WEATHER_AUDIO.is_file(),
        "news_audio": _first_existing(NEWS_AUDIO_CANDIDATES) is not None,
    }
