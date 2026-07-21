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
        "Draft an SMS/message. Requires confirm_message. Sending needs a provider.",
        {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
    ),
    _fn(
        "confirm_message",
        "Confirm or cancel a prepared message draft.",
        {
            "type": "object",
            "properties": {"confirmed": {"type": "boolean"}},
            "required": ["confirmed"],
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

    def prepare_message(self, text: str) -> str:
        cleaned = (text or "").strip()
        if not cleaned:
            return json.dumps({"ok": False, "error": "empty message"})
        self._message_draft = cleaned[:500]
        spoken = "Message drafted. Confirm to send."
        return json.dumps(
            {
                "ok": True,
                "announce": spoken,
                "draft": "message",
                "text": self._message_draft,
                "next": "confirm_message",
            }
        )

    def confirm_message(self, confirmed: bool) -> str:
        if self._message_draft is None:
            return json.dumps({"ok": False, "error": "no message draft; prepare first"})
        self._message_draft = None
        if not confirmed:
            return json.dumps({"ok": True, "announce": "Cancelled.", "sent": False})
        return json.dumps(
            {
                "ok": False,
                "announce": "Messaging is not configured.",
                "sent": False,
                "error": "messaging provider not configured; message not sent",
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
