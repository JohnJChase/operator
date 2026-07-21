"""GPT Realtime voice operator (WebSocket duplex). Local function tools only.

Supervisory logic is a small CO-style mode machine: one path, one state,
event → action (or nothing). Mechanism (ALSA / Piper / WS) stays thick;
the relay chart stays thin.
"""

from __future__ import annotations

import base64
import json
import queue
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from operator_os.audio import AudioRouter, resample_s16_mono
from operator_os.config import HardwareProfile
from operator_os.events import EventLog
from operator_os.local_tools import TOOL_DEFS, LocalTools, build_status_snapshot
from operator_os.openai_client import api_key_from_env

REALTIME_MODEL = "gpt-realtime-2.1"
REALTIME_RATE = 24_000
WS_URL = f"wss://api.openai.com/v1/realtime?model={REALTIME_MODEL}"

INSTRUCTIONS = """You are a silent switchboard. You do not chat and you never speak.

When the caller asks for time, weather, news, or status: call the matching tool.
If the request is unclear or is noise: call no tools and produce no output.
Outside line / messages: prepare then confirm only.
"""

UNAVAILABLE = (
    "The information operator is temporarily unavailable. "
    "Local services remain in operation. "
    "Dial 0 for the menu, 1 for news, 2 for weather, 9 for outside line."
)

_DEFAULT_REALTIME = {
    "vad_threshold": 0.35,
    "vad_silence_ms": 500,
    "vad_prefix_ms": 200,
    "noise_reduction": "near_field",
    "mic_gate_dbfs": -28,
    "mic_hangover_ms": 300,
    # Keep post-gain speech well below 0 dBFS — clipping wrecks transcription.
    "capture_gain": 5.0,
    "playback_gain": 0.55,
    "echo_guard_ms": 400,
}

# Brief hold after crossbar click before the announcement trunk talks.
_TRUNK_HOLD_S = 0.12
_TOOLS_FALLBACK_S = 1.2


class RealtimeMode(str, Enum):
    LISTEN = "LISTEN"
    THINKING = "THINKING"
    SPEAK = "SPEAK"
    ECHO = "ECHO"


@dataclass(frozen=True)
class RtEvent:
    type: str
    value: Any = None


@dataclass(frozen=True)
class RtTransition:
    mode: RealtimeMode
    actions: tuple[str, ...] = ()
    reason: str = ""


def _transition(mode: RealtimeMode, event: RtEvent) -> RtTransition:
    """Pure supervisory chart. Wrong signal for this state → no action."""
    et = event.type

    if et in ("hush", "hangup"):
        return RtTransition(RealtimeMode.ECHO, actions=("interrupt", "arm_echo"), reason=et)

    if et == "echo_elapsed":
        if mode == RealtimeMode.ECHO:
            return RtTransition(
                RealtimeMode.LISTEN,
                actions=("fx_release", "open_mic"),
                reason="echo_elapsed",
            )
        return RtTransition(mode)

    if et == "speak_done":
        if mode == RealtimeMode.SPEAK:
            return RtTransition(RealtimeMode.ECHO, actions=("arm_echo",), reason="speak_done")
        return RtTransition(mode)

    if et == "greet":
        # Patch onto the operator position — plant FX then she answers.
        if mode in (RealtimeMode.LISTEN, RealtimeMode.ECHO, RealtimeMode.THINKING):
            return RtTransition(
                RealtimeMode.SPEAK, actions=("fx_seize", "speak"), reason="greet"
            )
        if mode == RealtimeMode.SPEAK:
            return RtTransition(
                RealtimeMode.SPEAK,
                actions=("interrupt", "fx_seize", "speak"),
                reason="greet",
            )
        return RtTransition(mode)

    if et == "trunk_announce":
        if mode == RealtimeMode.SPEAK:
            return RtTransition(
                RealtimeMode.SPEAK,
                actions=("interrupt", "fx_seize", "speak"),
                reason="trunk_cut_in",
            )
        if mode in (RealtimeMode.LISTEN, RealtimeMode.THINKING, RealtimeMode.ECHO):
            return RtTransition(
                RealtimeMode.SPEAK, actions=("fx_seize", "speak"), reason="trunk_announce"
            )
        return RtTransition(mode)

    if et == "no_answer":
        # No trunk was seized — just reopen the jack (no fx_release).
        if mode == RealtimeMode.THINKING:
            return RtTransition(
                RealtimeMode.LISTEN,
                actions=("open_mic",),
                reason="no_answer",
            )
        return RtTransition(mode)

    if et == "speech_started":
        # New talker energy; clear intent bookkeeping in apply. Stay put.
        return RtTransition(mode, actions=("clear_intent",), reason="speech_started")

    if et == "speech_stopped":
        if mode == RealtimeMode.LISTEN:
            return RtTransition(
                RealtimeMode.THINKING, actions=("arm_fallback",), reason="speech_stopped"
            )
        return RtTransition(mode)

    if et == "transcript":
        if mode != RealtimeMode.THINKING:
            return RtTransition(mode)
        text = str(event.value or "").strip()
        if not text:
            return RtTransition(
                RealtimeMode.LISTEN, actions=("open_mic",), reason="empty_transcript"
            )
        intent = _local_intent(text)
        if intent:
            return RtTransition(
                mode, actions=("fulfill_intent",), reason=f"local:{intent}"
            )
        return RtTransition(mode, actions=("request_tools",), reason="transcript")

    if et == "transcription_failed":
        if mode == RealtimeMode.THINKING:
            return RtTransition(mode, actions=("request_tools",), reason="transcription_failed")
        return RtTransition(mode)

    if et == "tools_timeout":
        if mode == RealtimeMode.THINKING:
            return RtTransition(mode, actions=("request_tools",), reason="tools_timeout")
        return RtTransition(mode)

    if et == "response_done":
        if mode == RealtimeMode.SPEAK or mode == RealtimeMode.ECHO:
            return RtTransition(mode, actions=("ack_duplicate_tools",), reason="busy")
        if mode == RealtimeMode.THINKING:
            return RtTransition(mode, actions=("resolve_tools",), reason="response_done")
        return RtTransition(mode)

    return RtTransition(mode)


def _announce_from_tool_json(raw: str) -> str | None:
    """Pull announce/spoken text from a tool JSON result."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not data.get("ok", True):
        return None
    text = data.get("announce") or data.get("spoken")
    if not text or not isinstance(text, str):
        return None
    text = text.strip()
    return text or None


def _local_intent(transcript: str) -> str | None:
    """Map a short user transcript to a tool we can answer locally."""
    t = transcript.lower()
    if any(w in t for w in ("time", "clock", "o'clock", "what hour")):
        return "get_current_time"
    if "weather" in t or "temperature" in t or "forecast" in t:
        return "get_weather"
    if "news" in t or "headlines" in t:
        return "get_news"
    return None


def pcm_rms_dbfs(pcm_s16le: bytes) -> float:
    """RMS level of mono S16LE PCM in dBFS. Empty → -120."""
    import array
    import math

    if len(pcm_s16le) < 2:
        return -120.0
    samples = array.array("h")
    samples.frombytes(pcm_s16le[: len(pcm_s16le) - (len(pcm_s16le) % 2)])
    if not samples:
        return -120.0
    acc = 0.0
    for s in samples:
        acc += float(s) * float(s)
    rms = math.sqrt(acc / len(samples))
    if rms <= 1e-9:
        return -120.0
    return 20.0 * math.log10(rms / 32768.0)


def apply_gain_s16(pcm_s16le: bytes, gain: float) -> bytes:
    """Scale mono S16LE PCM by gain and clip."""
    import array

    if gain == 1.0 or len(pcm_s16le) < 2:
        return pcm_s16le
    samples = array.array("h")
    samples.frombytes(pcm_s16le[: len(pcm_s16le) - (len(pcm_s16le) % 2)])
    g = float(gain)
    for i, s in enumerate(samples):
        v = int(s * g)
        if v > 32767:
            v = 32767
        elif v < -32768:
            v = -32768
        samples[i] = v
    return samples.tobytes()


@dataclass
class RealtimeSession:
    audio: AudioRouter
    events: EventLog
    tools: LocalTools
    api_key: str
    realtime_cfg: dict[str, Any] = field(default_factory=dict)
    auto_greet: bool = True
    cancel: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _bay_thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _ws: Any = field(default=None, init=False, repr=False)
    _send_lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _ready: threading.Event = field(default_factory=threading.Event, init=False)
    mode: RealtimeMode = field(default=RealtimeMode.LISTEN, init=False)
    _bay: queue.SimpleQueue[RtEvent] = field(default_factory=queue.SimpleQueue, init=False)
    _mic_gate_dbfs: float = field(default=-40.0, init=False)
    _mic_hangover_ms: float = field(default=400.0, init=False)
    _mic_open_until: float = field(default=0.0, init=False)
    _capture_gain: float = field(default=8.0, init=False)
    _playback_gain: float = field(default=0.55, init=False)
    _echo_guard_ms: float = field(default=500.0, init=False)
    _echo_until: float = field(default=0.0, init=False)
    _tools_deadline: float | None = field(default=None, init=False)
    _speak_token: int = field(default=0, init=False)
    _intent_handled: str = field(default="", init=False)
    # True while playing the release click back onto the operator jack.
    _release_hold: bool = field(default=False, init=False)
    last_rms_dbfs: float = field(default=-120.0, init=False)
    last_raw_rms_dbfs: float = field(default=-120.0, init=False)
    uplink_open: bool = field(default=False, init=False)
    response_count: int = field(default=0, init=False)
    speech_started_count: int = field(default=0, init=False)
    cfg: dict[str, Any] = field(default_factory=dict, init=False)

    @property
    def echo_guarding(self) -> bool:
        return self.mode == RealtimeMode.ECHO

    @property
    def listening(self) -> bool:
        return self.mode == RealtimeMode.LISTEN

    @property
    def _piper_playing(self) -> bool:
        return self.mode == RealtimeMode.SPEAK

    @property
    def _model_speaking(self) -> bool:
        return self.mode == RealtimeMode.SPEAK

    @_model_speaking.setter
    def _model_speaking(self, value: bool) -> None:
        # Tune UI compat — real transitions go through the bay.
        if value:
            self.mode = RealtimeMode.SPEAK
        elif self.mode == RealtimeMode.SPEAK:
            self.mode = RealtimeMode.LISTEN

    def cancel_now(self) -> None:
        self.cancel.set()
        self._post(RtEvent("hangup"))
        self.audio.notify_hangup()
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def wait_ready(self, timeout: float = 10.0) -> bool:
        """True once duplex is up (safe to greet / talk)."""
        return self._ready.wait(timeout)

    def start(self) -> None:
        self._bay_thread = threading.Thread(target=self._bay_loop, name="realtime-bay", daemon=True)
        self._bay_thread.start()
        self._thread = threading.Thread(target=self._run, name="realtime-operator", daemon=True)
        self._thread.start()

    def _post(self, event: RtEvent) -> None:
        if self.cancel.is_set() and event.type not in ("hangup", "hush", "speak_done"):
            return
        self._bay.put(event)

    def _bay_loop(self) -> None:
        import time

        while not self.cancel.is_set():
            self._tick()
            try:
                event = self._bay.get(timeout=0.05)
            except queue.Empty:
                continue
            self._handle(event)
        # Drain release signals after cancel.
        while True:
            try:
                event = self._bay.get_nowait()
            except queue.Empty:
                break
            if event.type in ("hangup", "hush", "speak_done"):
                self._handle(event)

    def _tick(self) -> None:
        import time

        if self.cancel.is_set():
            return
        now = time.monotonic()
        if self.mode == RealtimeMode.ECHO and now >= self._echo_until:
            self._handle(RtEvent("echo_elapsed"))
            return
        if (
            self.mode == RealtimeMode.THINKING
            and self._tools_deadline is not None
            and now >= self._tools_deadline
        ):
            self._tools_deadline = None
            self._handle(RtEvent("tools_timeout"))

    def _handle(self, event: RtEvent) -> None:
        if event.type == "speech_started":
            self.speech_started_count += 1
            print(
                f"realtime: speech_started  rms={self.last_rms_dbfs:+.1f}dB",
                flush=True,
            )
        tr = _transition(self.mode, event)
        if tr.mode != self.mode:
            self.mode = tr.mode
            if tr.reason:
                print(f"realtime: mode={tr.mode.value}  ({tr.reason})", flush=True)
        for action in tr.actions:
            self._apply(action, event)

    def _apply(self, action: str, event: RtEvent) -> None:
        if action == "clear_intent":
            self._intent_handled = ""
            self._tools_deadline = None
            return
        if action == "arm_fallback":
            import time

            print("realtime: speech_stopped", flush=True)
            self._tools_deadline = time.monotonic() + _TOOLS_FALLBACK_S
            return
        if action == "arm_echo":
            import time

            self._echo_until = time.monotonic() + (self._echo_guard_ms / 1000.0)
            return
        if action.startswith("fx_"):
            if not self.cancel.is_set() and not self.audio.is_on_hook:
                print(f"realtime: plant {action}", flush=True)
                self._release_hold = action == "fx_release"
                try:
                    self.audio.play_plant(action, wait=True)
                    if action == "fx_seize":
                        import time

                        time.sleep(_TRUNK_HOLD_S)
                finally:
                    self._release_hold = False
            return
        if action == "open_mic":
            self._open_mic()
            return
        if action == "interrupt":
            self._interrupt_mouth()
            return
        if action == "speak":
            text = str(event.value or "Operator.")
            if text.strip():
                gain = 0.55 if event.type == "greet" else 0.45
                self._start_mouth(text, min_gain=gain)
            else:
                self._handle(RtEvent("speak_done"))
            return
        if action == "request_tools":
            self._request_tools()
            return
        if action == "fulfill_intent":
            self._fulfill_intent(str(event.value or ""))
            return
        if action == "resolve_tools":
            self._resolve_tools(event.value)
            return
        if action == "ack_duplicate_tools":
            self._ack_duplicate_tools(event.value)
            return

    def _fulfill_intent(self, transcript: str) -> None:
        intent = _local_intent(transcript)
        if not intent or intent == self._intent_handled:
            return
        self._intent_handled = intent
        self._tools_deadline = None
        self._send({"type": "response.cancel"})
        print(f"realtime: local intent {intent!r}", flush=True)
        result = self.tools.dispatch(intent, {})
        self.events.emit("realtime_tool", name=intent, detail=result[:120])
        print(f"realtime: tool {intent} (local intent)", flush=True)
        ann = _announce_from_tool_json(result)
        if not ann:
            print("realtime: tool returned no announce", flush=True)
            self._handle(RtEvent("no_answer"))
            return
        self._handle(RtEvent("trunk_announce", value=ann))

    def _resolve_tools(self, ws_event: Any) -> None:
        if not isinstance(ws_event, dict):
            self._handle(RtEvent("no_answer"))
            return
        resp = ws_event.get("response") or {}
        output = resp.get("output") or []
        calls = [
            item
            for item in output
            if isinstance(item, dict) and item.get("type") == "function_call"
        ]
        if not calls:
            self._handle(RtEvent("no_answer"))
            if self.tools.line_seized:
                self.cancel_now()
            return
        announces: list[str] = []
        for call in calls:
            if self.cancel.is_set():
                return
            name = str(call.get("name") or "")
            if name and name == self._intent_handled:
                self._send(
                    {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "function_call_output",
                            "call_id": call.get("call_id"),
                            "output": json.dumps({"ok": True, "handled_locally": True}),
                        },
                    }
                )
                continue
            raw_args = call.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except json.JSONDecodeError:
                args = {}
            if not isinstance(args, dict):
                args = {}
            result = self.tools.dispatch(name, args)
            self.events.emit("realtime_tool", name=name, detail=result[:120])
            print(f"realtime: tool {name}", flush=True)
            if name:
                self._intent_handled = name
            ann = _announce_from_tool_json(result)
            if ann:
                announces.append(ann)
            self._send(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": call.get("call_id"),
                        "output": result,
                    },
                }
            )
        if self.tools.line_seized:
            self.cancel_now()
            return
        if announces:
            self._handle(RtEvent("trunk_announce", value=" ".join(announces)))
        else:
            self._handle(RtEvent("no_answer"))

    def _ack_duplicate_tools(self, ws_event: Any) -> None:
        if not isinstance(ws_event, dict):
            return
        resp = ws_event.get("response") or {}
        output = resp.get("output") or []
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "function_call":
                continue
            name = str(item.get("name") or "")
            if name and name == self._intent_handled:
                self._send(
                    {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "function_call_output",
                            "call_id": item.get("call_id"),
                            "output": json.dumps({"ok": True, "handled_locally": True}),
                        },
                    }
                )

    def _request_tools(self) -> None:
        if self.cancel.is_set() or self.mode == RealtimeMode.SPEAK or self._intent_handled:
            return
        self._tools_deadline = None
        print("realtime: requesting tools", flush=True)
        self._send(
            {
                "type": "response.create",
                "response": {
                    "output_modalities": ["text"],
                    "tool_choice": "auto",
                    "instructions": (
                        "If the caller asked for time, weather, news, or status, "
                        "call that tool. Otherwise produce no output."
                    ),
                },
            }
        )

    def _interrupt_mouth(self) -> None:
        self._speak_token += 1
        self._send({"type": "response.cancel"})
        self.audio.reset_duplex_playback()
        self.uplink_open = False

    def _start_mouth(self, text: str, *, min_gain: float) -> None:
        if self.cancel.is_set() or self.audio.is_on_hook or not text.strip():
            self._handle(RtEvent("speak_done"))
            return
        self._speak_token += 1
        token = self._speak_token
        self.clear_input()
        threading.Thread(
            target=self._mouth_run,
            args=(token, text.strip(), min_gain),
            daemon=True,
            name="realtime-mouth",
        ).start()

    def _mouth_run(self, token: int, text: str, min_gain: float) -> None:
        """Piper only — plant FX already ran from the transition chart."""
        import time
        import wave

        try:
            if token != self._speak_token or self.cancel.is_set() or self.audio.is_on_hook:
                return
            self._send({"type": "response.cancel"})
            self.audio.reset_duplex_playback()
            print(f"realtime: piper {text!r}", flush=True)
            wav = self.audio.synthesize(text)
            if wav is None or token != self._speak_token or self.cancel.is_set():
                print("realtime: piper synthesize failed", flush=True)
                return
            with wave.open(str(wav), "rb") as wf:
                src_rate = wf.getframerate()
                pcm = wf.readframes(wf.getnframes())
            wav.unlink(missing_ok=True)
            if token != self._speak_token or self.cancel.is_set() or self.audio.is_on_hook:
                return
            gain = max(self._playback_gain, min_gain) if min_gain else self._playback_gain
            pcm = apply_gain_s16(pcm, gain)
            self.audio.write_duplex_playback(pcm, src_rate=src_rate)
            n_samples = max(1, len(pcm) // 2)
            time.sleep(min(8.0, n_samples / float(src_rate) + 0.15))
        finally:
            if token == self._speak_token:
                self._post(RtEvent("speak_done"))

    def _open_mic(self) -> None:
        """Open uplink after plant release — no FX here."""
        if self.cancel.is_set() or self.audio.is_on_hook:
            return
        self._mic_open_until = 0.0
        self.uplink_open = False
        self.clear_input()
        self.events.emit("realtime", value="listening")
        print("realtime: listening", flush=True)

    def _send(self, payload: dict[str, Any]) -> None:
        ws = self._ws
        if ws is None or self.cancel.is_set():
            return
        with self._send_lock:
            try:
                ws.send(json.dumps(payload))
            except Exception:
                pass

    def _run(self) -> None:
        import websocket

        self.events.emit("realtime", value="start")
        try:
            if self.audio.is_on_hook or self.cancel.is_set():
                return
            headers = [f"Authorization: Bearer {self.api_key}"]
            ws = websocket.WebSocketApp(
                WS_URL,
                header=headers,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )
            self._ws = ws
            ws.run_forever(ping_interval=20, ping_timeout=10)
        except Exception as e:
            self.events.emit("realtime", value="error", detail=str(e)[:160])
            if not self.cancel.is_set() and not self.audio.is_on_hook:
                try:
                    self.audio.speak(UNAVAILABLE, wait=True)
                except Exception:
                    pass
        finally:
            self._ws = None
            self.audio.stop()
            self.events.emit("realtime", value="end")

    def _on_open(self, ws: Any) -> None:
        import time

        self.cfg = {**_DEFAULT_REALTIME, **self.realtime_cfg}
        self._apply_local_cfg()
        self._send(self._session_update_event())

        handset_rate = self.audio.cfg.sample_rate_hz

        def on_capture(pcm: bytes) -> None:
            if self.cancel.is_set():
                return
            self.last_raw_rms_dbfs = pcm_rms_dbfs(pcm)
            pcm = apply_gain_s16(pcm, self._capture_gain)
            level = pcm_rms_dbfs(pcm)
            self.last_rms_dbfs = level
            now = time.monotonic()

            if self.mode != RealtimeMode.LISTEN or self._release_hold:
                self.uplink_open = False
                return

            if level >= self._mic_gate_dbfs:
                self._mic_open_until = now + (self._mic_hangover_ms / 1000.0)
            open_ = now <= self._mic_open_until
            self.uplink_open = open_
            if not open_:
                return
            pcm24 = (
                pcm
                if handset_rate == REALTIME_RATE
                else resample_s16_mono(pcm, handset_rate, REALTIME_RATE)
            )
            self._send(
                {
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(pcm24).decode("ascii"),
                }
            )

        try:
            self.audio.start_duplex(on_capture)
        except Exception as e:
            self.events.emit("realtime", value="duplex_fail", detail=str(e)[:120])
            self.cancel_now()
            return

        self._ready.set()
        if self.auto_greet:
            self.greet()
        else:
            self.events.emit("realtime", value="ready")
            print("realtime: ready (awaiting greet)", flush=True)

    def _apply_local_cfg(self) -> None:
        c = self.cfg
        self._mic_gate_dbfs = float(c.get("mic_gate_dbfs", -40))
        self._mic_hangover_ms = float(c.get("mic_hangover_ms", 250))
        self._capture_gain = max(0.1, float(c.get("capture_gain", 8.0)))
        self._playback_gain = max(0.05, min(1.0, float(c.get("playback_gain", 0.55))))
        self._echo_guard_ms = max(0.0, float(c.get("echo_guard_ms", 400)))

    def _session_update_event(self) -> dict[str, Any]:
        c = self.cfg
        noise = c.get("noise_reduction", "near_field")
        input_audio: dict[str, Any] = {
            "format": {"type": "audio/pcm", "rate": REALTIME_RATE},
            "transcription": {"model": "gpt-4o-mini-transcribe", "language": "en"},
            "turn_detection": {
                "type": "server_vad",
                "threshold": float(c.get("vad_threshold", 0.45)),
                "prefix_padding_ms": int(c.get("vad_prefix_ms", 200)),
                "silence_duration_ms": int(c.get("vad_silence_ms", 350)),
                "create_response": False,
                "interrupt_response": True,
            },
        }
        if noise and str(noise).lower() not in ("", "off", "none", "null"):
            input_audio["noise_reduction"] = {"type": str(noise)}
        session: dict[str, Any] = {
            "type": "realtime",
            "model": REALTIME_MODEL,
            "output_modalities": ["text"],
            "instructions": INSTRUCTIONS,
            "tools": TOOL_DEFS,
            "tool_choice": "auto",
            "audio": {
                "input": input_audio,
            },
        }
        return {"type": "session.update", "session": session}

    def update_cfg(self, **fields: Any) -> dict[str, Any]:
        """Live-tune knobs; pushes session.update for server-side fields."""
        for k, v in fields.items():
            if v is None:
                continue
            self.cfg[k] = v
        self._apply_local_cfg()
        self._send(self._session_update_event())
        return dict(self.cfg)

    def greet(self) -> None:
        """Local Piper 'Operator.' — already on the operator jack (no trunk click)."""
        self._post(RtEvent("greet", value="Operator."))

    def hush(self) -> None:
        """Release the talking path and clear bleed (Space in tune UI)."""
        if self.cancel.is_set():
            return
        self._post(RtEvent("hush"))
        print("realtime: hush", flush=True)

    def clear_input(self) -> None:
        self._send({"type": "input_audio_buffer.clear"})
        self._mic_open_until = 0.0
        self.uplink_open = False

    def interrupt(self) -> None:
        """Stop mouth immediately (Space in tune UI)."""
        self.hush()
        self.events.emit("realtime", value="interrupt")

    def save_cfg(self, profile_path: str | Path = "config/hardware_profile.yaml") -> Path:
        """Write current realtime knobs into the hardware profile YAML."""
        import yaml

        path = Path(profile_path)
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        keys = (
            "vad_threshold",
            "vad_silence_ms",
            "vad_prefix_ms",
            "noise_reduction",
            "mic_gate_dbfs",
            "mic_hangover_ms",
            "capture_gain",
            "playback_gain",
            "echo_guard_ms",
        )
        block = dict(data.get("realtime") or {})
        for k in keys:
            if k in self.cfg:
                block[k] = self.cfg[k]
        for dead in ("barge_dbfs", "voice", "max_output_tokens"):
            block.pop(dead, None)
        data["realtime"] = block
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        return path

    def _on_message(self, ws: Any, message: str | bytes) -> None:
        if self.cancel.is_set():
            return
        try:
            event = json.loads(message if isinstance(message, str) else message.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        et = event.get("type")
        if et == "response.created":
            self.response_count += 1
            return
        if et == "input_audio_buffer.speech_started":
            self._post(RtEvent("speech_started"))
            return
        if et == "input_audio_buffer.speech_stopped":
            self._post(RtEvent("speech_stopped"))
            return
        if et == "conversation.item.input_audio_transcription.completed":
            transcript = str(event.get("transcript") or "")
            print(f"realtime: heard {transcript!r}", flush=True)
            self._post(RtEvent("transcript", value=transcript))
            return
        if et == "conversation.item.input_audio_transcription.failed":
            err = event.get("error") or event
            print(f"realtime: transcription failed {err!r}", flush=True)
            self._post(RtEvent("transcription_failed"))
            return
        if et == "response.done":
            self._post(RtEvent("response_done", value=event))
            return
        if et == "error":
            err = event.get("error") or {}
            msg = str(err.get("message") or err)
            if "cancel" in msg.lower():
                return
            print(f"realtime: api_error {msg[:160]}", flush=True)
            self.events.emit("realtime", value="api_error", detail=msg[:160])
            return

    def _on_error(self, ws: Any, error: Exception) -> None:
        if not self.cancel.is_set():
            self.events.emit("realtime", value="ws_error", detail=str(error)[:160])

    def _on_close(self, ws: Any, status_code: int | None = None, msg: str | None = None) -> None:
        self.cancel.set()
        self.audio.stop()


def start_realtime(
    audio: AudioRouter,
    events: EventLog,
    *,
    profile: HardwareProfile,
) -> RealtimeSession | None:
    from operator_os.refresh import load_dotenv

    load_dotenv()
    key = api_key_from_env()
    if not key:
        return None
    tools = LocalTools(
        audio=audio,
        profile=profile,
        status_snapshot=build_status_snapshot(profile.name),
        voice_mode=True,
    )
    session = RealtimeSession(
        audio=audio,
        events=events,
        tools=tools,
        api_key=key,
        realtime_cfg=dict(profile.raw.get("realtime") or {}),
    )
    session.start()
    return session


def realtime_text_smoke(api_key: str, user_text: str, *, profile: HardwareProfile) -> str:
    """Non-mic smoke: Realtime WS + local tools, text modalities only."""
    import time

    import websocket

    audio = AudioRouter(profile.audio)
    tools = LocalTools(
        audio=audio,
        profile=profile,
        status_snapshot=build_status_snapshot(profile.name),
        voice_mode=True,
    )
    done = threading.Event()
    reply_parts: list[str] = []
    err: list[str] = []
    ws_holder: list[Any] = []

    def send(payload: dict[str, Any]) -> None:
        ws_holder[0].send(json.dumps(payload))

    def on_open(ws: Any) -> None:
        ws_holder.append(ws)
        send(
            {
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "model": REALTIME_MODEL,
                    "output_modalities": ["text"],
                    "instructions": INSTRUCTIONS,
                    "tools": TOOL_DEFS,
                    "tool_choice": "auto",
                },
            }
        )
        send(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": user_text}],
                },
            }
        )
        send({"type": "response.create"})

    def on_message(ws: Any, message: str | bytes) -> None:
        event = json.loads(message if isinstance(message, str) else message.decode("utf-8"))
        et = event.get("type")
        if et == "response.output_text.delta":
            reply_parts.append(str(event.get("delta") or ""))
            return
        if et == "response.done":
            resp = event.get("response") or {}
            output = resp.get("output") or []
            calls = [
                item
                for item in output
                if isinstance(item, dict) and item.get("type") == "function_call"
            ]
            if calls:
                for call in calls:
                    name = str(call.get("name") or "")
                    raw_args = call.get("arguments") or "{}"
                    try:
                        args = json.loads(raw_args) if isinstance(raw_args, str) else {}
                    except json.JSONDecodeError:
                        args = {}
                    result = tools.dispatch(name, args if isinstance(args, dict) else {})
                    send(
                        {
                            "type": "conversation.item.create",
                            "item": {
                                "type": "function_call_output",
                                "call_id": call.get("call_id"),
                                "output": result,
                            },
                        }
                    )
                send({"type": "response.create"})
                return
            for item in output:
                if not isinstance(item, dict) or item.get("type") != "message":
                    continue
                for part in item.get("content") or []:
                    if isinstance(part, dict) and part.get("type") in ("output_text", "text"):
                        t = str(part.get("text") or "")
                        if t:
                            reply_parts.append(t)
            done.set()
            try:
                ws.close()
            except Exception:
                pass
            return
        if et == "error":
            err.append(str((event.get("error") or {}).get("message") or event)[:200])
            done.set()
            try:
                ws.close()
            except Exception:
                pass

    def on_error(ws: Any, error: Exception) -> None:
        err.append(str(error)[:200])
        done.set()

    ws = websocket.WebSocketApp(
        WS_URL,
        header=[f"Authorization: Bearer {api_key}"],
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
    )
    thread = threading.Thread(target=lambda: ws.run_forever(ping_interval=20), daemon=True)
    thread.start()
    if not done.wait(timeout=45.0):
        ws.close()
        audio.close()
        raise TimeoutError("realtime text smoke timed out")
    time.sleep(0.2)
    audio.close()
    if err and not reply_parts:
        raise RuntimeError(err[0])
    return "".join(reply_parts).strip()
