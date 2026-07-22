"""Telephone state machine. Deterministic transitions; no GPIO callbacks here.

Named states own the plant. Each state has a jumper patch (see
``operator_os.plant.STATE_PATCH``); entering a state installs that patch via
``Plant.apply``. Features add chart states/edges — they do not open ALSA.

Cradle down enters HOOK_PENDING (silence first); flash vs hangup is
discriminated after the cut. Soft flags are not modes.

Chart edges in CHART_EDGES are the docs/source checklist; _transition implements
them (plus digit-value branches). `just chart` regenerates docs/state-chart.md.
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
    HOOK_PENDING = "HOOK_PENDING"  # cradle down; silence; flash vs hangup
    SMS_ALERTING = "SMS_ALERTING"  # double-ring + pickup window
    MEET_CHOOSING = "MEET_CHOOSING"  # digit-7 menu; rotary 1–N selects a Meet
    DIAGNOSTIC = "DIAGNOSTIC"
    ERROR = "ERROR"


# Off-hook plant states that cradle-down can suspend.
_OFF_HOOK_RESUME = frozenset(
    {
        State.DIAL_TONE,
        State.COLLECTING_DIGIT,
        State.PLAYING_SERVICE,
        State.OUTSIDE_LINE,
        State.SIP_CALL,
        State.MEET_CHOOSING,
    }
)


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


@dataclass(frozen=True)
class ChartEdge:
    """One documented chart arc (from, event_label, to)."""

    source: State
    event: str
    dest: State
    actions: tuple[str, ...] = ()
    note: str = ""


# Curated edges for Mermaid + drift checks. Runtime digit branches are expanded.
CHART_EDGES: tuple[ChartEdge, ...] = (
    ChartEdge(State.ON_HOOK_IDLE, "off_hook", State.DIAL_TONE, ("dial_tone",)),
    ChartEdge(State.ON_HOOK_IDLE, "ring_start", State.INCOMING_RINGING, ("ring_start",)),
    ChartEdge(State.ON_HOOK_IDLE, "sms_alert", State.SMS_ALERTING, ("ring_sms",)),
    ChartEdge(State.SMS_ALERTING, "pickup_timeout", State.ON_HOOK_IDLE, ("ring_stop",)),
    ChartEdge(
        State.SMS_ALERTING,
        "off_hook",
        State.PLAYING_SERVICE,
        ("ring_stop", "announce_sms"),
    ),
    ChartEdge(
        State.INCOMING_RINGING,
        "off_hook",
        State.SIP_CALL,
        ("ring_stop", "sip_answer"),
    ),
    ChartEdge(
        State.INCOMING_RINGING,
        "voicemail_answer",
        State.VOICEMAIL,
        ("ring_stop", "sip_answer"),
    ),
    ChartEdge(
        State.INCOMING_RINGING,
        "incoming_cancel",
        State.ON_HOOK_IDLE,
        ("ring_stop", "sip_hangup"),
    ),
    ChartEdge(State.VOICEMAIL, "off_hook", State.SIP_CALL),
    ChartEdge(State.VOICEMAIL, "vm_done", State.ON_HOOK_IDLE, ("sip_hangup",)),
    ChartEdge(State.DIAL_TONE, "pulse", State.COLLECTING_DIGIT, ("audio_stop",)),
    ChartEdge(State.DIAL_TONE, "digit_9", State.OUTSIDE_LINE, ("audio_stop", "fx_outside")),
    ChartEdge(
        State.DIAL_TONE,
        "digit",
        State.PLAYING_SERVICE,
        ("audio_stop", "fx_seize", "play_service"),
    ),
    ChartEdge(State.COLLECTING_DIGIT, "digit_9", State.OUTSIDE_LINE, ("fx_outside",)),
    ChartEdge(
        State.COLLECTING_DIGIT,
        "digit",
        State.PLAYING_SERVICE,
        ("fx_seize", "play_service"),
    ),
    ChartEdge(State.OUTSIDE_LINE, "place_call", State.SIP_CALL, ("audio_stop", "sip_dial")),
    ChartEdge(
        State.OUTSIDE_LINE,
        "outside_cancel",
        State.DIAL_TONE,
        ("audio_stop", "fx_release", "dial_tone"),
    ),
    ChartEdge(
        State.SIP_CALL,
        "sip_done",
        State.DIAL_TONE,
        ("sip_hangup", "fx_release", "dial_tone"),
    ),
    ChartEdge(
        State.PLAYING_SERVICE,
        "place_call",
        State.SIP_CALL,
        ("audio_stop", "sip_dial"),
    ),
    ChartEdge(
        State.PLAYING_SERVICE,
        "service_done",
        State.DIAL_TONE,
        ("fx_release", "dial_tone"),
    ),
    ChartEdge(
        State.PLAYING_SERVICE,
        "meet_choose",
        State.MEET_CHOOSING,
        ("announce_meet_choices",),
    ),
    ChartEdge(
        State.MEET_CHOOSING,
        "digit",
        State.SIP_CALL,
        ("audio_stop", "sip_dial"),
    ),
    ChartEdge(
        State.MEET_CHOOSING,
        "meet_timeout",
        State.DIAL_TONE,
        ("fx_release", "dial_tone"),
    ),
    ChartEdge(
        State.MEET_CHOOSING,
        "meet_cancel",
        State.DIAL_TONE,
        ("fx_release", "dial_tone"),
    ),
    ChartEdge(State.MEET_CHOOSING, "cradle_down", State.HOOK_PENDING, ("audio_stop",)),
    ChartEdge(State.DIAL_TONE, "cradle_down", State.HOOK_PENDING, ("audio_stop",)),
    ChartEdge(State.COLLECTING_DIGIT, "cradle_down", State.HOOK_PENDING, ("audio_stop",)),
    ChartEdge(State.PLAYING_SERVICE, "cradle_down", State.HOOK_PENDING, ("audio_stop",)),
    ChartEdge(State.OUTSIDE_LINE, "cradle_down", State.HOOK_PENDING, ("audio_stop",)),
    ChartEdge(State.SIP_CALL, "cradle_down", State.HOOK_PENDING, ("audio_stop",)),
    ChartEdge(
        State.HOOK_PENDING,
        "hangup",
        State.ON_HOOK_IDLE,
        ("audio_stop", "ring_stop", "sip_hangup"),
    ),
    # flash_resume destinations (dynamic resume_state) — all documented:
    ChartEdge(State.HOOK_PENDING, "flash_resume", State.DIAL_TONE, ("dial_tone",)),
    ChartEdge(State.HOOK_PENDING, "flash_resume", State.PLAYING_SERVICE, ("resume_service",)),
    ChartEdge(State.HOOK_PENDING, "flash_resume", State.OUTSIDE_LINE),
    ChartEdge(State.HOOK_PENDING, "flash_resume", State.SIP_CALL),
    ChartEdge(State.HOOK_PENDING, "flash_resume", State.COLLECTING_DIGIT),
    ChartEdge(State.HOOK_PENDING, "flash_resume", State.MEET_CHOOSING),
)


@dataclass
class PhoneController:
    state: State = State.ON_HOOK_IDLE
    last_digit: int | None = None
    resume_state: State | None = None
    history: list[tuple[State, State, str]] = field(default_factory=list)

    def handle(self, event: Event) -> Transition:
        prev = self.state
        if event.type == "cradle_down" and prev in _OFF_HOOK_RESUME:
            self.resume_state = prev
        result = _transition(self.state, event, resume=self.resume_state)
        if result.state != prev:
            self.history.append((prev, result.state, result.reason))
            self.state = result.state
        if result.reason in ("hangup", "flash_resume", "cradle_bounce"):
            if result.state != State.HOOK_PENDING:
                self.resume_state = None
        if event.type == "digit" and isinstance(event.value, int):
            self.last_digit = event.value
        return result


def _service_actions(digit: Any, *, stop_audio: bool) -> tuple[str, ...]:
    """Plant FX + play_service for a committed office digit (not outside collect)."""
    prefix: tuple[str, ...] = ("audio_stop",) if stop_audio else ()
    if digit == 9:
        return prefix + ("fx_outside",)
    return prefix + ("fx_seize", "play_service")


def _resume_actions(dest: State) -> tuple[str, ...]:
    if dest == State.DIAL_TONE:
        return ("dial_tone",)
    if dest == State.PLAYING_SERVICE:
        return ("resume_service",)
    return ()


def _hangup_transition() -> Transition:
    return Transition(
        State.ON_HOOK_IDLE,
        actions=("audio_stop", "ring_stop", "sip_hangup"),
        reason="hangup",
    )


def _transition(
    state: State,
    event: Event,
    *,
    resume: State | None = None,
) -> Transition:
    et = event.type

    # Hangup / on-hook wins from every state (including HOOK_PENDING).
    if et in ("on_hook", "hangup"):
        return _hangup_transition()

    if state == State.HOOK_PENDING:
        if et in ("hook_flash", "hook_flash_2", "cradle_bounce"):
            dest = resume if resume in _OFF_HOOK_RESUME else State.DIAL_TONE
            reason = "cradle_bounce" if et == "cradle_bounce" else "flash_resume"
            return Transition(dest, actions=_resume_actions(dest), reason=reason)
        return Transition(state)

    # Unbound flash elsewhere: accepted no-op (session skip is plant-side).
    if et in ("hook_flash", "hook_flash_2", "cradle_bounce"):
        return Transition(state, reason=et)

    if et == "cradle_down":
        if state in _OFF_HOOK_RESUME:
            return Transition(
                State.HOOK_PENDING,
                actions=("audio_stop",),
                reason="cradle_down",
            )
        return Transition(state)

    if state == State.ON_HOOK_IDLE:
        if et == "off_hook":
            return Transition(State.DIAL_TONE, actions=("dial_tone",), reason="off_hook")
        if et == "ring_start":
            return Transition(
                State.INCOMING_RINGING, actions=("ring_start",), reason="incoming"
            )
        if et == "sms_alert":
            return Transition(
                State.SMS_ALERTING, actions=("ring_sms",), reason="sms_alert"
            )
        return Transition(state)

    if state == State.SMS_ALERTING:
        if et == "off_hook":
            return Transition(
                State.PLAYING_SERVICE,
                actions=("ring_stop", "announce_sms"),
                reason="sms_pickup",
            )
        if et == "pickup_timeout":
            return Transition(
                State.ON_HOOK_IDLE,
                actions=("ring_stop",),
                reason="sms_missed",
            )
        if et == "sms_alert":
            return Transition(state, reason="sms_busy")
        if et == "ring_start":
            # Inbound voice wins over SMS alert ring.
            return Transition(
                State.INCOMING_RINGING,
                actions=("ring_stop", "ring_start"),
                reason="incoming",
            )
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
        if et == "meet_choose":
            return Transition(
                State.MEET_CHOOSING,
                actions=("announce_meet_choices",),
                reason="meet_choose",
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

    if state == State.MEET_CHOOSING:
        # Rotary collect is owned by this state; digit commits a menu choice.
        if et == "pulse":
            return Transition(state, reason="meet_pulse")
        if et == "digit":
            n = event.value
            if isinstance(n, int) and 1 <= n <= 9:
                return Transition(
                    State.SIP_CALL,
                    actions=("audio_stop", "sip_dial"),
                    reason=f"meet_digit_{n}",
                )
            return Transition(
                State.DIAL_TONE,
                actions=("fx_release", "dial_tone"),
                reason="meet_cancel",
            )
        if et in ("meet_timeout", "meet_cancel"):
            return Transition(
                State.DIAL_TONE,
                actions=("fx_release", "dial_tone"),
                reason=et,
            )
        return Transition(state)

    if state == State.ERROR:
        if et == "off_hook":
            return Transition(State.DIAL_TONE, actions=("dial_tone",), reason="recover")
        return Transition(state)

    if state == State.DIAGNOSTIC:
        return Transition(state)

    return Transition(state)


def render_mermaid() -> str:
    """Mermaid stateDiagram from CHART_EDGES (+ hangup note)."""
    lines = [
        "```mermaid",
        "stateDiagram-v2",
        "  direction TB",
    ]
    seen: set[tuple[str, str, str]] = set()
    for edge in CHART_EDGES:
        key = (edge.source.value, edge.event, edge.dest.value)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"  {edge.source.value} --> {edge.dest.value}: {edge.event}")
    lines.append("  note right of HOOK_PENDING")
    lines.append("    cradle_down cuts audio;")
    lines.append("    flash resumes resume_state;")
    lines.append("    hangup → idle")
    lines.append("  end note")
    lines.append("```")
    return "\n".join(lines) + "\n"


def write_state_chart(path: str = "docs/state-chart.md") -> str:
    """Write docs/state-chart.md; return the path written."""
    from pathlib import Path

    body = (
        "# Telephone state chart\n\n"
        "Generated from `operator_os.state.CHART_EDGES` — do not hand-edit the diagram.\n"
        "Regenerate with `just chart`.\n\n"
        "Rules:\n\n"
        "- Named states own the plant. Each state has a cordboard patch "
        "(`operator_os.plant.STATE_PATCH`); see `docs/audio-line.md`.\n"
        "- New capabilities = chart states/edges + patch rows — not ALSA hacks "
        "in feature code.\n"
        "- Cradle down enters `HOOK_PENDING` (silence first); flash vs hangup "
        "is decided after the cut.\n"
        "- If a bug story is a race, queue order, or “forgot to stop audio,” "
        "the chart or patch table is wrong.\n\n"
        f"{render_mermaid()}"
    )
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body, encoding="utf-8")
    return str(out)
