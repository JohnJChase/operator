"""AI operator session: local tools only; OpenAI stays behind HTTP.

Hangup sets cancel + audio.notify_hangup(); this loop checks cancel between steps.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from operator_os.audio import AudioRouter
from operator_os.events import EventLog
from operator_os.openai_client import (
    OpenAIError,
    api_key_from_env,
    create_response,
    function_calls,
    output_text,
    transcribe_wav,
)
from operator_os.services import (
    NEWS_AUDIO_CANDIDATES,
    NEWS_JSON,
    WEATHER_AUDIO,
    WEATHER_CACHE,
    _first_existing,
    _json_spoken,
)

MODEL = "gpt-4o-mini"
LISTEN_SECONDS = 5.0
MAX_TURNS = 6
MAX_TOOL_ROUNDS = 4

INSTRUCTIONS = """You are the information operator for a Western Electric 302 telephone.
Speak briefly and clearly — replies are read aloud over a handset.
You may only use the provided tools. You cannot access GPIO, shell, or raw APIs.
For outside line or messages: always prepare first, ask the caller to confirm,
then call the matching confirm_* tool. Never claim a call or message was placed
unless confirm_* succeeded. If a tool says a capability is unavailable, say so.
Local services: weather, news, device status, outside-line seize (after confirm).
"""

TOOL_DEFS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "get_weather",
        "description": "Read the cached Weather Bureau report text.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "get_news",
        "description": "Read the cached News of the Day text.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "get_device_status",
        "description": "Safe local device/cache status (no secrets).",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "play_weather",
        "description": "Play the cached weather announcement on the handset.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "play_news",
        "description": "Play the cached news announcement on the handset.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "prepare_outside_line",
        "description": "Draft an outside-line seize. Requires confirm_outside_line next.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "confirm_outside_line",
        "description": "Confirm or cancel a prepared outside-line seize.",
        "parameters": {
            "type": "object",
            "properties": {"confirmed": {"type": "boolean"}},
            "required": ["confirmed"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "prepare_message",
        "description": "Draft an SMS/message. Requires confirm_message. Sending needs a provider.",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "confirm_message",
        "description": "Confirm or cancel a prepared message draft.",
        "parameters": {
            "type": "object",
            "properties": {"confirmed": {"type": "boolean"}},
            "required": ["confirmed"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


@dataclass
class LocalTools:
    """Policy layer the model cannot bypass."""

    audio: AudioRouter
    status_snapshot: dict[str, Any] = field(default_factory=dict)
    _outside_draft: bool = False
    _message_draft: str | None = None
    line_seized: bool = False

    def dispatch(self, name: str, arguments: dict[str, Any]) -> str:
        fn = {
            "get_weather": self.get_weather,
            "get_news": self.get_news,
            "get_device_status": self.get_device_status,
            "play_weather": self.play_weather,
            "play_news": self.play_news,
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
        return json.dumps({"ok": True, "spoken": text})

    def get_news(self) -> str:
        text = _json_spoken(NEWS_JSON)
        if not text:
            return json.dumps({"ok": False, "error": "news not on file"})
        return json.dumps({"ok": True, "spoken": text})

    def get_device_status(self) -> str:
        return json.dumps({"ok": True, "status": self.status_snapshot})

    def play_weather(self) -> str:
        if WEATHER_AUDIO.is_file() and WEATHER_AUDIO.stat().st_size > 0:
            self.audio.play_file(WEATHER_AUDIO, wait=True)
            return json.dumps({"ok": True, "played": "weather.wav"})
        text = _json_spoken(WEATHER_CACHE)
        if text:
            self.audio.speak(text, wait=True)
            return json.dumps({"ok": True, "spoken": True})
        return json.dumps({"ok": False, "error": "weather not on file"})

    def play_news(self) -> str:
        audio = _first_existing(NEWS_AUDIO_CANDIDATES)
        if audio is not None:
            self.audio.play_file(audio, wait=True)
            return json.dumps({"ok": True, "played": audio.name})
        text = _json_spoken(NEWS_JSON)
        if text:
            self.audio.speak(text, wait=True)
            return json.dumps({"ok": True, "spoken": True})
        return json.dumps({"ok": False, "error": "news not on file"})

    def prepare_outside_line(self) -> str:
        self._outside_draft = True
        return json.dumps(
            {
                "ok": True,
                "draft": "outside_line",
                "next": "Ask the caller to confirm, then confirm_outside_line.",
            }
        )

    def confirm_outside_line(self, confirmed: bool) -> str:
        if not self._outside_draft:
            return json.dumps({"ok": False, "error": "no outside-line draft; prepare first"})
        self._outside_draft = False
        if not confirmed:
            return json.dumps({"ok": True, "seized": False, "reason": "cancelled"})
        self.audio.seize_outside_line()
        self.line_seized = True
        return json.dumps({"ok": True, "seized": True})

    def prepare_message(self, text: str) -> str:
        cleaned = (text or "").strip()
        if not cleaned:
            return json.dumps({"ok": False, "error": "empty message"})
        self._message_draft = cleaned[:500]
        return json.dumps(
            {
                "ok": True,
                "draft": "message",
                "text": self._message_draft,
                "next": "Ask the caller to confirm, then confirm_message.",
            }
        )

    def confirm_message(self, confirmed: bool) -> str:
        if self._message_draft is None:
            return json.dumps({"ok": False, "error": "no message draft; prepare first"})
        self._message_draft = None
        if not confirmed:
            return json.dumps({"ok": True, "sent": False, "reason": "cancelled"})
        # SMS/Telnyx not built yet — confirmation gate still enforced.
        return json.dumps(
            {
                "ok": False,
                "sent": False,
                "error": "messaging provider not configured; message not sent",
            }
        )


@dataclass
class OperatorSession:
    audio: AudioRouter
    events: EventLog
    tools: LocalTools
    api_key: str
    cancel: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)

    def cancel_now(self) -> None:
        self.cancel.set()
        self.audio.notify_hangup()

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="ai-operator", daemon=True)
        self._thread.start()

    def run_text_turn(self, user_text: str) -> str:
        """One text turn (CLI/tests). Speaks the reply if off-hook audio allows."""
        reply = self._complete(user_text)
        if reply and not self.cancel.is_set():
            try:
                self.audio.set_hook(True)
                self.audio.speak(reply, wait=True)
            except Exception:
                pass
        return reply

    def _run(self) -> None:
        try:
            self.events.emit("operator", value="start")
            self.audio.set_hook(True)
            greeting = "Operator. Go ahead, please."
            self.audio.speak(greeting, wait=True)
            if self.cancel.is_set():
                return
            for _ in range(MAX_TURNS):
                if self.cancel.is_set():
                    return
                # Brief gap so the handset is quiet before listen.
                time.sleep(0.3)
                if self.cancel.is_set():
                    return
                wav = Path("data/recordings/operator-listen.wav")
                try:
                    self.audio.record(LISTEN_SECONDS, wav)
                except Exception as e:
                    self.events.emit("operator", value="record_fail", detail=str(e)[:120])
                    return
                if self.cancel.is_set():
                    return
                try:
                    user = transcribe_wav(wav, api_key=self.api_key)
                except OpenAIError as e:
                    self.events.emit("operator", value="stt_fail", detail=str(e)[:120])
                    self.audio.speak(
                        "I am having trouble hearing you. Please try again later.",
                        wait=True,
                    )
                    return
                self.events.emit("operator", value="heard", detail=user[:80])
                if self._looks_like_goodbye(user):
                    self.audio.speak("Goodbye.", wait=True)
                    return
                reply = self._complete(user)
                if self.cancel.is_set() or self.tools.line_seized:
                    return
                if not reply:
                    self.audio.speak("I did not catch that.", wait=True)
                    continue
                self.audio.speak(reply, wait=True)
                if self.tools.line_seized:
                    return
            if not self.cancel.is_set():
                self.audio.speak("Please hang up or dial again for further assistance.", wait=True)
        except Exception as e:
            self.events.emit("operator", value="error", detail=str(e)[:160])
            try:
                self.audio.speak(
                    "The operator is temporarily unavailable. Local services remain in operation.",
                    wait=True,
                )
            except Exception:
                pass
        finally:
            self.events.emit("operator", value="end")

    def _complete(self, user_text: str) -> str:
        input_list: list[dict[str, Any]] = [{"role": "user", "content": user_text}]
        for _ in range(MAX_TOOL_ROUNDS):
            if self.cancel.is_set():
                return ""
            resp = create_response(
                api_key=self.api_key,
                model=MODEL,
                input=input_list,
                tools=TOOL_DEFS,
                instructions=INSTRUCTIONS,
            )
            calls = function_calls(resp)
            if not calls:
                return output_text(resp)
            # Append model output items, then our tool results (Responses API).
            for item in resp.get("output") or []:
                if isinstance(item, dict):
                    input_list.append(item)
            for call in calls:
                if self.cancel.is_set():
                    return ""
                name = str(call.get("name") or "")
                raw_args = call.get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except json.JSONDecodeError:
                    args = {}
                result = self.tools.dispatch(name, args if isinstance(args, dict) else {})
                self.events.emit("operator_tool", name=name, detail=result[:120])
                input_list.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.get("call_id"),
                        "output": result,
                    }
                )
        return "One moment — please try a shorter request."

    @staticmethod
    def _looks_like_goodbye(text: str) -> bool:
        t = text.lower().strip()
        return t in ("goodbye", "good bye", "bye", "hang up", "that's all", "thats all")


def build_status_snapshot(profile_name: str) -> dict[str, Any]:
    return {
        "profile": profile_name,
        "weather_cache": WEATHER_CACHE.is_file(),
        "news_cache": NEWS_JSON.is_file(),
        "weather_audio": WEATHER_AUDIO.is_file(),
        "news_audio": _first_existing(NEWS_AUDIO_CANDIDATES) is not None,
    }


def start_operator(
    audio: AudioRouter,
    events: EventLog,
    *,
    profile_name: str,
) -> OperatorSession | None:
    """Start digit-0 operator session, or None if AI unavailable (caller speaks fallback)."""
    from operator_os.refresh import load_dotenv

    load_dotenv()
    key = api_key_from_env()
    if not key:
        return None
    tools = LocalTools(audio=audio, status_snapshot=build_status_snapshot(profile_name))
    session = OperatorSession(audio=audio, events=events, tools=tools, api_key=key)
    session.start()
    return session


UNAVAILABLE = (
    "The operator is temporarily unavailable. "
    "Local services remain in operation. "
    "Dial 1 for news, 2 for weather, 9 for outside line."
)
