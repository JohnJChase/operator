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
    OUTSIDE_LINE = "OUTSIDE_LINE"  # seized trunk; collecting destination digits
    SIP_CALL = "SIP_CALL"  # live Telnyx call
    VOICEMAIL = "VOICEMAIL"  # on-hook miss: SIP answered; handset mic must stay dead
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
    """Plant FX + play_service for a committed office digit (not outside collect)."""
    prefix: tuple[str, ...] = ("audio_stop",) if stop_audio else ()
    if digit == 9:
        return prefix + ("fx_outside",)
    return prefix + ("fx_seize", "play_service")


def _transition(state: State, event: Event) -> Transition:
    et = event.type

    # Hangup / on-hook wins from every state.
    if et in ("on_hook", "hangup"):
        actions: tuple[str, ...] = ("audio_stop", "ring_stop", "sip_hangup")
        return Transition(State.ON_HOOK_IDLE, actions=actions, reason="hangup")

    # Hook flash / double-flash: valid inputs everywhere; default is no-op.
    # Later blocks bind these (info desk end-utterance, VM next, in-call menus).
    if et in ("hook_flash", "hook_flash_2"):
        return Transition(state, reason=et)

    if state == State.ON_HOOK_IDLE:
        if et == "off_hook":
            return Transition(State.DIAL_TONE, actions=("dial_tone",), reason="off_hook")
        if et == "ring_start":
            return Transition(State.INCOMING_RINGING, actions=("ring_start",), reason="incoming")
        return Transition(state)

    if state == State.INCOMING_RINGING:
        if et == "off_hook":
            return Transition(
                State.SIP_CALL,
                actions=("ring_stop", "sip_answer"),
                reason="answered",
            )
        if et == "voicemail_answer":
            return Transition(
                State.VOICEMAIL,
                actions=("ring_stop", "sip_answer"),
                reason="voicemail",
            )
        if et in ("ring_stop", "incoming_cancel"):
            return Transition(
                State.ON_HOOK_IDLE,
                actions=("ring_stop", "sip_hangup"),
                reason="incoming_cancel" if et == "incoming_cancel" else "ring_stop",
            )
        return Transition(state)

    if state == State.VOICEMAIL:
        # Cradle is already down; off-hook intercepts into a live call.
        if et == "off_hook":
            return Transition(State.SIP_CALL, reason="vm_intercept")
        if et == "vm_done":
            return Transition(
                State.ON_HOOK_IDLE,
                actions=("sip_hangup",),
                reason="vm_done",
            )
        return Transition(state)

    if state == State.DIAL_TONE:
        if et == "digit":
            if event.value == 9:
                return Transition(
                    State.OUTSIDE_LINE,
                    actions=("audio_stop", "fx_outside"),
                    reason="digit_9",
                )
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
            if event.value == 9:
                return Transition(
                    State.OUTSIDE_LINE,
                    actions=("fx_outside",),
                    reason="digit_9",
                )
            return Transition(
                State.PLAYING_SERVICE,
                actions=_service_actions(event.value, stop_audio=False),
                reason=f"digit_{event.value}",
            )
        return Transition(state)

    if state == State.OUTSIDE_LINE:
        # Destination digits are collected by the main loop; FSM only leaves on
        # place_call / cancel / hangup.
        if et == "place_call":
            return Transition(
                State.SIP_CALL,
                actions=("audio_stop", "sip_dial"),
                reason="place_call",
            )
        if et == "outside_cancel":
            return Transition(
                State.DIAL_TONE,
                actions=("audio_stop", "fx_release", "dial_tone"),
                reason="outside_cancel",
            )
        if et == "pulse":
            # Classic CO: dial tone cuts on first dial pulse, not after the digit.
            return Transition(state, actions=("audio_stop",), reason="outside_pulse")
        if et == "digit":
            return Transition(state, reason="outside_digit")
        return Transition(state)

    if state == State.SIP_CALL:
        if et == "sip_done":
            return Transition(
                State.DIAL_TONE,
                actions=("sip_hangup", "fx_release", "dial_tone"),
                reason="sip_done",
            )
        return Transition(state)

    if state == State.PLAYING_SERVICE:
        if et == "place_call":
            return Transition(
                State.SIP_CALL,
                actions=("audio_stop", "sip_dial"),
                reason="join_meeting",
            )
        if et == "service_done":
            return Transition(
                State.DIAL_TONE,
                actions=("fx_release", "dial_tone"),
                reason="service_done",
            )
        if et == "digit":
            if event.value == 9:
                return Transition(
                    State.OUTSIDE_LINE,
                    actions=("audio_stop", "fx_outside"),
                    reason="digit_9",
                )
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
