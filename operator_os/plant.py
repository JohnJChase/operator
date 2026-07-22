"""Plant cordboard: terminals + jumpers. Sole owner of handset/line audio topology.

Chart states declare a Patch; ``Plant.apply`` installs it. Features must not open
ALSA, alsaloop, or amixer — they change state (and fill ProgramContext).

Terminals (conceptual):
  Receiver, Mic — physical ATR2x
  LineRx, LineTx — softphone on snd-aloop
  DialTone / Stream / File / Speak — program sources into Receiver
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from operator_os.state import State


class ReceiverFeed(str, Enum):
    """What (if anything) drives the earpiece."""

    NONE = "none"
    DIAL_TONE = "dial_tone"
    STUTTER_DIAL = "stutter_dial"
    LINE = "line"  # Sip LineRx → Receiver (bridge play leg)
    PROGRAM = "program"  # stream / file / speak / fx via context
    SERVICE = "service"  # info desk / mailbox session owns AudioRouter


class MicFeed(str, Enum):
    """Where the carbon mic goes (if anywhere)."""

    NONE = "none"
    LINE = "line"  # Mic → LineTx (bridge capture leg)


@dataclass(frozen=True)
class Patch:
    """Jumper patch for one chart state. Mic and receiver are independent."""

    receiver: ReceiverFeed = ReceiverFeed.NONE
    mic: MicFeed = MicFeed.NONE
    program_kind: str = ""  # stream | file | speak | fx
    program_ref: str = ""
    label: str = ""

    @property
    def both_legs(self) -> bool:
        return self.receiver == ReceiverFeed.LINE and self.mic == MicFeed.LINE


# Chart → patch shape (program payload comes from PlantContext).
STATE_PATCH: dict[State, Patch] = {
    State.ON_HOOK_IDLE: Patch(label="idle"),
    State.INCOMING_RINGING: Patch(label="ringing"),
    State.SMS_ALERTING: Patch(label="sms_alert"),
    State.DIAL_TONE: Patch(receiver=ReceiverFeed.DIAL_TONE, label="dial_tone"),
    State.COLLECTING_DIGIT: Patch(label="collect"),
    State.PLAYING_SERVICE: Patch(receiver=ReceiverFeed.SERVICE, label="service"),
    State.OUTSIDE_LINE: Patch(receiver=ReceiverFeed.DIAL_TONE, label="outside"),
    State.SIP_CALL: Patch(
        receiver=ReceiverFeed.LINE, mic=MicFeed.LINE, label="sip_live"
    ),
    State.VOICEMAIL: Patch(label="voicemail"),
    State.HOOK_PENDING: Patch(label="hook_pending"),
    State.MEET_CHOOSING: Patch(receiver=ReceiverFeed.SERVICE, label="meet_choose"),
    State.DIAGNOSTIC: Patch(label="diagnostic"),
    State.ERROR: Patch(label="error"),
}


@dataclass
class PlantContext:
    """Mutable session data the chart does not store (URLs, MWI, etc.)."""

    stutter_dial: bool = False
    stutter_s: float = 2.5
    program_kind: str = ""
    program_ref: str = ""
    sip_mic_capture: int = 12
    off_hook: bool = False
    # How SIP_CALL jumpers are realized on this hardware:
    #   bridge  — pjsua on Loopback + alsaloop (inbound live answer)
    #   handset — pjsua on USB directly (outbound; ATR2x can't sustain alsaloop)
    sip_line_mode: str = "bridge"


@dataclass
class Plant:
    """Cordboard. ``apply`` is the only way main should change cradle audio topology."""

    audio: Any  # AudioRouter
    bridge: Any  # HandsetBridge
    live: bool = True
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _patch: Patch = field(default_factory=Patch, init=False)
    _guard: Any = field(default=None, init=False)
    _ctx: PlantContext = field(default_factory=PlantContext, init=False)

    @property
    def patch(self) -> Patch:
        return self._patch

    @property
    def context(self) -> PlantContext:
        return self._ctx

    def apply_state(self, state: State, *, ctx: PlantContext | None = None) -> None:
        """Install the patch for ``state`` (chart entry)."""
        if ctx is not None:
            self._ctx = ctx
        base = STATE_PATCH.get(state, Patch(label=state.value))
        c = self._ctx
        if base.receiver == ReceiverFeed.DIAL_TONE and c.stutter_dial:
            patch = Patch(
                receiver=ReceiverFeed.STUTTER_DIAL,
                mic=base.mic,
                label=base.label + "+mwi",
            )
        else:
            patch = base
        self.apply(patch)

    def apply(self, patch: Patch) -> None:
        """Make the board match ``patch``."""
        with self._lock:
            if not self.live:
                self._patch = patch
                return
            prev = self._patch
            self._patch = patch
            off_hook = bool(self._ctx.off_hook)

            if patch.label == "idle":
                self._drop_line_legs()
                self.audio.notify_hangup()
                return

            if patch.label in ("hook_pending", "voicemail", "ringing", "sms_alert"):
                self._drop_line_legs()
                self.audio.stop()
                return

            if patch.label == "collect":
                self._drop_line_legs()
                self.audio.stop()
                return

            if not off_hook and (
                patch.receiver != ReceiverFeed.NONE or patch.mic != MicFeed.NONE
            ):
                self._drop_line_legs()
                self.audio.stop()
                return

            if patch.both_legs:
                # Same chart jumpers (LineRx↔Receiver, Mic↔LineTx); realization
                # depends on hardware. Outbound Meet was silent on alsaloop.
                mode = (self._ctx.sip_line_mode or "bridge").strip().lower()
                if mode == "handset":
                    self._bridge_stop()
                    if not prev.both_legs or prev.label != patch.label:
                        self.audio.stop()
                        time.sleep(0.35)
                    self._hygiene_arm()
                    return
                if not prev.both_legs or not self.bridge.active:
                    self.audio.stop()
                    time.sleep(0.35)
                    self._hygiene_arm()
                    self._bridge_start()
                return

            # Local receiver programs — no SIP bridge.
            self._drop_line_legs()
            if patch.receiver == ReceiverFeed.DIAL_TONE:
                self.audio.set_hook(True)
                self.audio.stop()
                self.audio.play_tone("dial", wait=False)
                return
            if patch.receiver == ReceiverFeed.STUTTER_DIAL:
                self.audio.set_hook(True)
                self.audio.stop()
                self.audio.play_stutter_dial(self._ctx.stutter_s, wait=False)
                return
            if patch.receiver == ReceiverFeed.PROGRAM:
                self.audio.set_hook(True)
                self.audio.stop()
                self._start_program(patch)
                return
            if patch.receiver == ReceiverFeed.SERVICE:
                self.audio.set_hook(True)
                if prev.receiver != ReceiverFeed.SERVICE:
                    self.audio.stop()
                # Feature starts content after the transition (desk, stream, …).
                return
            self.audio.stop()

    def play_fx(self, action: str) -> None:
        """One-shot plant FX on the receiver (seize/release)."""
        if not self.live or not self._ctx.off_hook:
            return
        with self._lock:
            self.audio.play_plant(action, wait=True)

    def clear_handset(self) -> None:
        self.apply(Patch(label="hook_pending"))

    def snapshot(self) -> dict[str, Any]:
        p = self._patch
        return {
            "patch": p.label,
            "receiver": p.receiver.value,
            "mic": p.mic.value,
            "bridge": bool(self.bridge.active),
            "sip_line_mode": self._ctx.sip_line_mode,
            "program_kind": p.program_kind or self._ctx.program_kind,
            "program_ref": (p.program_ref or self._ctx.program_ref)[:80],
        }

    def _drop_line_legs(self) -> None:
        self._bridge_stop()
        self._hygiene_release()

    def _start_program(self, patch: Patch) -> None:
        kind = patch.program_kind or self._ctx.program_kind
        ref = patch.program_ref or self._ctx.program_ref
        if kind == "stream" and ref:
            self.audio.play_stream(ref, wait=False)
        elif kind == "file" and ref:
            from pathlib import Path

            self.audio.play_file(Path(ref), wait=False)
        elif kind == "speak" and ref:
            self.audio.speak(ref, wait=False)
        elif kind == "fx" and ref:
            self.audio.play_plant(ref, wait=True)

    def _bridge_start(self) -> None:
        if self.bridge.active:
            return
        try:
            self.bridge.start()
            print("sip: handset bridge up (line ↔ cradle)", flush=True)
        except Exception as e:
            print(f"sip: handset bridge failed {e}", flush=True)

    def _bridge_stop(self) -> None:
        if self.bridge.active:
            self.bridge.stop()
            print("sip: handset bridge down", flush=True)

    def _hygiene_arm(self) -> None:
        if self._guard is not None:
            return
        try:
            from operator_os.handset_bridge import HandsetSipGuard

            g = HandsetSipGuard(
                handset_alsa=getattr(self.bridge, "handset_alsa", "plughw:2,0"),
                capture_level=int(self._ctx.sip_mic_capture),
            )
            g.arm()
            self._guard = g
        except Exception as e:
            print(f"plant: hygiene arm failed {e}", flush=True)

    def _hygiene_release(self) -> None:
        g = self._guard
        self._guard = None
        if g is not None:
            try:
                g.release()
            except Exception:
                pass
