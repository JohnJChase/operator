"""Telephone state machine. Deterministic transitions; no GPIO callbacks here.

Plant audio (fx_seize / fx_release / fx_outside) lives on the transition
actions — the chart throws the switch when the circuit changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class State(str, Enum):
    ON_HOOK_IDLE = "ON_HOOK_IDLE"
    INCOMING_RINGING = "INCOMING_RINGING"
    DIAL_TONE = "DIAL_TONE"
    COLLECTING_DIGIT = "COLLECTING_DIGIT"
    PLAYING_SERVICE = "PLAYING_SERVICE"
    DIAGNOSTIC = "DIAGNOSTIC"
    ERROR = "ERROR"


@dataclass(frozen=True)
class Event:
    type: str
    value: Any = None
    reason: str | None = None


@dataclass(frozen=True)
class Transition:
    state: State
    actions: tuple[str, ...] = ()
    reason: str = ""


@dataclass
class PhoneController:
    state: State = State.ON_HOOK_IDLE
    last_digit: int | None = None
    history: list[tuple[State, State, str]] = field(default_factory=list)

    def handle(self, event: Event) -> Transition:
        prev = self.state
        result = _transition(self.state, event)
        if result.state != prev:
            self.history.append((prev, result.state, result.reason))
            self.state = result.state
        if event.type == "digit" and isinstance(event.value, int):
            self.last_digit = event.value
        return result


def _service_actions(digit: Any, *, stop_audio: bool) -> tuple[str, ...]:
    """Plant FX + play_service (or outside seize) for a committed digit."""
    prefix: tuple[str, ...] = ("audio_stop",) if stop_audio else ()
    if digit == 9:
        return prefix + ("fx_outside",)
    return prefix + ("fx_seize", "play_service")


def _transition(state: State, event: Event) -> Transition:
    et = event.type

    # Hangup / on-hook wins from every state.
    if et in ("on_hook", "hangup"):
        return Transition(
            State.ON_HOOK_IDLE,
            actions=("audio_stop", "ring_stop"),
            reason="hangup",
        )

    if state == State.ON_HOOK_IDLE:
        if et == "off_hook":
            return Transition(State.DIAL_TONE, actions=("dial_tone",), reason="off_hook")
        if et == "ring_start":
            return Transition(State.INCOMING_RINGING, actions=("ring_start",), reason="incoming")
        return Transition(state)

    if state == State.INCOMING_RINGING:
        if et == "off_hook":
            return Transition(
                State.DIAL_TONE,
                actions=("ring_stop", "dial_tone"),
                reason="answered",
            )
        if et == "ring_stop":
            return Transition(State.ON_HOOK_IDLE, actions=("ring_stop",), reason="ring_stop")
        return Transition(state)

    if state == State.DIAL_TONE:
        if et == "digit":
            return Transition(
                State.PLAYING_SERVICE,
                actions=_service_actions(event.value, stop_audio=True),
                reason=f"digit_{event.value}",
            )
        if et == "pulse":
            return Transition(
                State.COLLECTING_DIGIT,
                actions=("audio_stop",),
                reason="first_pulse",
            )
        return Transition(state)

    if state == State.COLLECTING_DIGIT:
        if et == "digit":
            return Transition(
                State.PLAYING_SERVICE,
                actions=_service_actions(event.value, stop_audio=False),
                reason=f"digit_{event.value}",
            )
        return Transition(state)

    if state == State.PLAYING_SERVICE:
        if et == "service_done":
            return Transition(
                State.DIAL_TONE,
                actions=("fx_release", "dial_tone"),
                reason="service_done",
            )
        if et == "digit":
            return Transition(
                State.PLAYING_SERVICE,
                actions=_service_actions(event.value, stop_audio=True),
                reason=f"digit_{event.value}",
            )
        return Transition(state)

    if state == State.ERROR:
        if et == "off_hook":
            return Transition(State.DIAL_TONE, actions=("dial_tone",), reason="recover")
        return Transition(state)

    if state == State.DIAGNOSTIC:
        return Transition(state)

    return Transition(state)
