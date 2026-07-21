"""Hook, dial, and ring I/O. Simulator and GPIO adapters share one shape."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from operator_os.config import HardwareProfile
from operator_os.dial import DialDecoder


PulseCallback = Callable[[], None]
HookCallback = Callable[[bool], None]


class PhoneIO:
    """Common phone I/O surface used by the state loop."""

    def is_off_hook(self) -> bool:
        raise NotImplementedError

    def ring_start(self) -> None:
        raise NotImplementedError

    def ring_stop(self) -> None:
        raise NotImplementedError

    def on_pulse(self, callback: PulseCallback) -> None:
        raise NotImplementedError

    def on_hook_change(self, callback: HookCallback) -> None:
        raise NotImplementedError

    def close(self) -> None:
        return


@dataclass
class SimulatorPhone(PhoneIO):
    """In-memory phone for tests and `operator-os simulate`."""

    profile: HardwareProfile
    off_hook: bool = False
    ringing: bool = False
    decoder: DialDecoder = field(init=False)
    _pulse_cb: PulseCallback | None = field(default=None, init=False, repr=False)
    _hook_cb: HookCallback | None = field(default=None, init=False, repr=False)
    pulses_seen: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.decoder = DialDecoder(digit_done_ms=self.profile.dial.digit_done_ms)

    def is_off_hook(self) -> bool:
        return self.off_hook

    def set_hook(self, off_hook: bool) -> None:
        if off_hook == self.off_hook:
            return
        self.off_hook = off_hook
        if off_hook and self.ringing:
            self.ring_stop()
        if self._hook_cb:
            self._hook_cb(off_hook)

    def inject_pulses(self, count: int, now_ms: float | None = None) -> None:
        base = now_ms if now_ms is not None else time.monotonic() * 1000
        for i in range(count):
            t = base + i * 50
            self.pulses_seen.append(t)
            self.decoder.pulse(t)
            if self._pulse_cb:
                self._pulse_cb()

    def inject_digit(self, digit: int, now_ms: float | None = None) -> None:
        pulses = 10 if digit == 0 else digit
        self.inject_pulses(pulses, now_ms=now_ms)

    def ring_start(self) -> None:
        if self.off_hook:
            return
        self.ringing = True

    def ring_stop(self) -> None:
        self.ringing = False

    def on_pulse(self, callback: PulseCallback) -> None:
        self._pulse_cb = callback

    def on_hook_change(self, callback: HookCallback) -> None:
        self._hook_cb = callback


@dataclass
class GpioPhone(PhoneIO):
    """Rev A GPIO: hook BCM17, dial BCM10 when_pressed, ring BCM23."""

    profile: HardwareProfile
    decoder: DialDecoder = field(init=False)
    _pulse_cb: PulseCallback | None = field(default=None, init=False, repr=False)
    _hook_cb: HookCallback | None = field(default=None, init=False, repr=False)
    _hook: object = field(default=None, init=False, repr=False)
    _dial: object = field(default=None, init=False, repr=False)
    _ring: object = field(default=None, init=False, repr=False)
    _ring_thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _ring_stop: threading.Event = field(default_factory=threading.Event, init=False)
    _closed: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        from gpiozero import Button, OutputDevice

        self.decoder = DialDecoder(digit_done_ms=self.profile.dial.digit_done_ms)
        pins = self.profile.gpio
        dial_bounce = self.profile.dial.pulse_debounce_ms / 1000.0
        hook_bounce = self.profile.hook.debounce_ms / 1000.0

        # active_low / pull_up: contact to GND = pressed
        self._hook = Button(
            pins.hook_bcm,
            pull_up=True,
            bounce_time=hook_bounce,
        )
        self._dial = Button(
            pins.dial_pulse_bcm,
            pull_up=True,
            bounce_time=dial_bounce,
        )
        self._ring = OutputDevice(pins.ring_bcm, active_high=True, initial_value=False)

        self._dial.when_pressed = self._on_dial_pressed
        self._hook.when_pressed = self._on_hook_pressed  # off-hook (to GND)
        self._hook.when_released = self._on_hook_released  # on-hook

    def is_off_hook(self) -> bool:
        # pressed = contact closed to GND = off-hook (LOW)
        return bool(self._hook.is_pressed)  # type: ignore[union-attr]

    def ring_start(self) -> None:
        if self.is_off_hook():
            return
        self._ring_stop.clear()
        if self._ring_thread and self._ring_thread.is_alive():
            return
        self._ring_thread = threading.Thread(target=self._ring_loop, daemon=True)
        self._ring_thread.start()

    def ring_stop(self) -> None:
        self._ring_stop.set()
        if self._ring is not None:
            self._ring.off()  # type: ignore[union-attr]

    def on_pulse(self, callback: PulseCallback) -> None:
        self._pulse_cb = callback

    def on_hook_change(self, callback: HookCallback) -> None:
        self._hook_cb = callback

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.ring_stop()
        for dev in (self._hook, self._dial, self._ring):
            if dev is not None:
                try:
                    dev.close()  # type: ignore[union-attr]
                except Exception:
                    pass

    def _on_dial_pressed(self) -> None:
        now_ms = time.monotonic() * 1000
        self.decoder.pulse(now_ms)
        if self._pulse_cb:
            self._pulse_cb()

    def _on_hook_pressed(self) -> None:
        # Off-hook: software ring cutoff immediately.
        self.ring_stop()
        if self._hook_cb:
            self._hook_cb(True)

    def _on_hook_released(self) -> None:
        if self._hook_cb:
            self._hook_cb(False)

    def _ring_loop(self) -> None:
        on_ms = self.profile.ring.cadence_on_ms
        off_ms = self.profile.ring.cadence_off_ms
        poll = self.profile.ring.poll_hook_while_ringing_ms / 1000.0
        while not self._ring_stop.is_set():
            if self.is_off_hook():
                self.ring_stop()
                return
            self._ring.on()  # type: ignore[union-attr]
            deadline = time.monotonic() + on_ms / 1000.0
            while time.monotonic() < deadline:
                if self._ring_stop.is_set() or self.is_off_hook():
                    self.ring_stop()
                    return
                time.sleep(poll)
            self._ring.off()  # type: ignore[union-attr]
            deadline = time.monotonic() + off_ms / 1000.0
            while time.monotonic() < deadline:
                if self._ring_stop.is_set() or self.is_off_hook():
                    self.ring_stop()
                    return
                time.sleep(poll)
