"""Turn-based information desk (digit 8): record → STT → tools → Piper."""

from __future__ import annotations

import json
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from operator_os.audio import AudioRouter
from operator_os.config import HardwareProfile
from operator_os.events import EventLog
from operator_os.local_tools import TOOL_DEFS, LocalTools, build_status_snapshot
from operator_os.openai_client import api_key_from_env, chat_with_tools, transcribe_wav

UNAVAILABLE = (
    "The information desk is temporarily unavailable. "
    "Please try again later, or dial zero for the local menu."
)

INSTRUCTIONS = """You are the information desk for a Western Electric 302 telephone exchange
(1930s–40s central-office feel). Speak briefly and clearly for a handset.
Use tools for facts (time, weather, news, status). Prefer one useful answer,
not chatter. Do not invent calendar, SMS, or voicemail results you cannot tool.
For SMS use prepare_message / confirm_message / list_messages / read_message.
For voicemail use list_voicemails / play_voicemail / delete_voicemail / callback_voicemail.
Outside line and messages require prepare then confirm."""

GREETING = "Information."
MAX_RECORD_S = 6.0
SILENCE_END_MS = 700
MAX_TURNS = 8


@dataclass
class InfoDeskSession:
    audio: AudioRouter
    events: EventLog
    profile: HardwareProfile
    tools: LocalTools = field(init=False)
    cancel: threading.Event = field(default_factory=threading.Event, init=False)
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.tools = LocalTools(
            audio=self.audio,
            profile=self.profile,
            status_snapshot=build_status_snapshot(self.profile.name),
            voice_mode=True,
        )

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def cancel_now(self) -> None:
        self.cancel.set()
        self.audio.notify_hangup()

    def wait_done(self, timeout: float | None = None) -> None:
        t = self._thread
        if t is not None:
            t.join(timeout=timeout)

    def _run(self) -> None:
        key = api_key_from_env()
        if not key:
            self.audio.speak(UNAVAILABLE, wait=True)
            return
        try:
            if self.cancel.is_set() or self.audio.is_on_hook:
                return
            self.audio.speak(GREETING, wait=True)
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": INSTRUCTIONS},
            ]
            for _ in range(MAX_TURNS):
                if self.cancel.is_set() or self.audio.is_on_hook:
                    return
                text = self._listen_once(key)
                if self.cancel.is_set() or self.audio.is_on_hook:
                    self.audio.stop()
                    return
                if not text:
                    self.audio.stop()
                    self.audio.speak("I did not catch that.", wait=True)
                    continue
                self.events.emit("info_desk", value="heard", detail=text[:120])
                print(f"info_desk: heard {text[:80]!r}", flush=True)
                messages.append({"role": "user", "content": text})
                try:
                    reply = self._complete(messages, key)
                finally:
                    self.audio.stop()
                if self.cancel.is_set() or self.audio.is_on_hook:
                    return
                if reply:
                    self.audio.speak(reply, wait=True)
                    messages.append({"role": "assistant", "content": reply})
                if self.tools.line_seized:
                    return
        except Exception as e:
            self.audio.stop()
            self.events.emit("info_desk", value="error", detail=str(e)[:120])
            if not self.audio.is_on_hook and not self.cancel.is_set():
                try:
                    self.audio.speak(UNAVAILABLE, wait=True)
                except Exception:
                    pass

    def _listen_once(self, api_key: str) -> str:
        with tempfile.TemporaryDirectory(prefix="operator-desk-") as tmp:
            wav = Path(tmp) / "utterance.wav"
            try:
                self.audio.record_utterance(
                    wav,
                    max_s=MAX_RECORD_S,
                    silence_end_ms=SILENCE_END_MS,
                )
            except RuntimeError:
                return ""
            if self.cancel.is_set() or self.audio.is_on_hook:
                return ""
            if not wav.is_file() or wav.stat().st_size < 1000:
                return ""
            # Hold chime covers STT + tool chat until we speak the answer.
            self.audio.play_thinking()
            self.events.emit("info_desk", value="thinking")
            try:
                return transcribe_wav(wav, api_key)
            except Exception as e:
                self.audio.stop()
                self.events.emit("info_desk", value="stt_error", detail=str(e)[:120])
                return ""

    def _complete(self, messages: list[dict[str, Any]], api_key: str) -> str:
        # Tool loop — cap rounds.
        for _ in range(6):
            if self.cancel.is_set() or self.audio.is_on_hook:
                return ""
            msg = chat_with_tools(messages, TOOL_DEFS, api_key)
            tool_calls = msg.get("tool_calls") or []
            content = (msg.get("content") or "").strip()
            if not tool_calls:
                return content
            messages.append(msg)
            for call in tool_calls:
                fn = call.get("function") or {}
                name = fn.get("name") or ""
                raw_args = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except json.JSONDecodeError:
                    args = {}
                result = self.tools.dispatch(name, args if isinstance(args, dict) else {})
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id") or name,
                        "content": result,
                    }
                )
                # Prefer announce fields for spoken reply after tools.
                try:
                    parsed = json.loads(result)
                    announce = parsed.get("announce") or parsed.get("spoken")
                    if announce and not tool_calls[1:]:
                        # Single tool with announce — speak that and stop.
                        return str(announce)
                except json.JSONDecodeError:
                    pass
        return content


def start_info_desk(
    audio: AudioRouter,
    events: EventLog,
    *,
    profile: HardwareProfile,
) -> InfoDeskSession | None:
    if not api_key_from_env():
        return None
    session = InfoDeskSession(audio=audio, events=events, profile=profile)
    session.start()
    return session


def info_desk_text_smoke(text: str, *, profile: HardwareProfile, audio: AudioRouter) -> str:
    """CLI: one text turn through tools (no mic). Returns spoken reply."""
    key = api_key_from_env()
    if not key:
        raise RuntimeError("OPENAI_API_KEY not set")
    audio.set_hook(True)  # off-hook for tool path (default AudioRouter is on-hook)
    tools = LocalTools(
        audio=audio,
        profile=profile,
        status_snapshot=build_status_snapshot(profile.name),
        voice_mode=True,
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": INSTRUCTIONS},
        {"role": "user", "content": text},
    ]
    session = InfoDeskSession(audio=audio, events=EventLog(), profile=profile)
    session.tools = tools
    return session._complete(messages, key) or "(no reply)"
