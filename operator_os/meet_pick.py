"""Digit-7 Meet menu speech (state MEET_CHOOSING owns the choice)."""

from __future__ import annotations

from operator_os.audio import AudioRouter
from operator_os.google_calendar import MeetDialIn

_ORDINAL = (
    "",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
)

# How long MEET_CHOOSING waits for a rotary digit after the menu.
MEET_CHOOSE_S = 30.0


def _short_title(title: str, *, limit: int = 48) -> str:
    t = " ".join((title or "").split()) or "the meeting"
    if len(t) > limit:
        return t[: limit - 1].rstrip() + "…"
    return t


def speak_meet_choices(audio: AudioRouter, meetings: list[MeetDialIn]) -> None:
    """Ask which Meet to join; chart state MEET_CHOOSING accepts dial 1–N."""
    n = min(len(meetings), 9)
    if n <= 0:
        return
    parts = ["Do you want to join:"]
    for i in range(n):
        word = _ORDINAL[i + 1]
        parts.append(f"Number {word}, {_short_title(meetings[i].title)}.")
    parts.append("Dial to choose.")
    audio.speak(" ".join(parts), wait=True)
