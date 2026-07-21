"""Pulse-to-digit decoder. Pulse-only; no off-normal."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DialDecoder:
    """Count dial return pulses; emit a digit after digit_done_ms of silence.

    Contact is open at rest and closes to GND on each return pulse.
    Count when_pressed events. Do not skip a leading pulse as wind-up.
    10 pulses => digit 0.
    """

    digit_done_ms: int = 700
    _pulses: int = field(default=0, init=False, repr=False)
    _last_pulse_ms: float | None = field(default=None, init=False, repr=False)

    def reset(self) -> None:
        self._pulses = 0
        self._last_pulse_ms = None

    def pulse(self, now_ms: float) -> None:
        self._pulses += 1
        self._last_pulse_ms = now_ms

    def poll(self, now_ms: float) -> int | None:
        """If a digit is complete, return it (0-9) and reset; else None."""
        if self._pulses == 0 or self._last_pulse_ms is None:
            return None
        if now_ms - self._last_pulse_ms < self.digit_done_ms:
            return None
        digit = pulses_to_digit(self._pulses)
        self.reset()
        return digit

    @property
    def pending_pulses(self) -> int:
        return self._pulses


def pulses_to_digit(pulses: int) -> int | None:
    """Map pulse count to dial digit. 10 -> 0. Invalid counts -> None."""
    if pulses == 10:
        return 0
    if 1 <= pulses <= 9:
        return pulses
    return None
