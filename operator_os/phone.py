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


@dataclass
class HookFlashClassifier:
    """Split cradle dips into chart events using profile timings.

    Cradle down emits ``cradle_down`` immediately (plant goes HOOK_PENDING /
    silence). Discrimination happens after the cut: short lift → flash;
    bounce → cradle_bounce (resume without session skip); stay down past
    hangup_min → on_hook.
    """

    flash_min_s: float
    flash_max_s: float
    hangup_min_s: float
    double_window_s: float = 0.45
    _pending_onhook_at: float | None = field(default=None, init=False, repr=False)
    _last_flash_at: float | None = field(default=None, init=False, repr=False)
    _hangup_emitted: bool = field(default=False, init=False, repr=False)

    @classmethod
    def from_profile(cls, profile: HardwareProfile) -> "HookFlashClassifier":
        h = profile.hook
        return cls(
            flash_min_s=h.flash_min_ms / 1000.0,
            flash_max_s=h.flash_max_ms / 1000.0,
            hangup_min_s=h.hangup_min_ms / 1000.0,
        )

    def feed(self, off_hook: bool, *, now: float | None = None) -> list[str]:
        """Raw hook edge → cradle_down / off_hook / hook_flash / cradle_bounce / on_hook."""
        t = time.monotonic() if now is None else now
        out: list[str] = []
        if off_hook:
            if self._pending_onhook_at is None:
                out.append("off_hook")
                self._hangup_emitted = False
                return out
            dt = t - self._pending_onhook_at
            self._pending_onhook_at = None
            if self._hangup_emitted:
                self._hangup_emitted = False
                out.append("off_hook")
                return out
            if self.flash_min_s <= dt <= self.flash_max_s:
                if (
                    self._last_flash_at is not None
                    and (t - self._last_flash_at) <= self.double_window_s
                ):
                    out.append("hook_flash_2")
                else:
                    out.append("hook_flash")
                self._last_flash_at = t
                return out
            if dt < self.flash_min_s:
                # Contact bounce while intending to stay off-hook: resume only.
                return ["cradle_bounce"]
            out.append("on_hook")
            out.append("off_hook")
            self._hangup_emitted = False
            return out

        # Going on-hook: cut now; hangup comes from poll() if they stay down.
        self._pending_onhook_at = t
        self._hangup_emitted = False
        return ["cradle_down"]

    def poll(self, *, on_hook: bool, now: float | None = None) -> list[str]:
        """While cradle is down, emit on_hook once hangup_min elapses."""
        t = time.monotonic() if now is None else now
        if not on_hook or self._pending_onhook_at is None or self._hangup_emitted:
            return []
        if t - self._pending_onhook_at >= self.hangup_min_s:
            self._hangup_emitted = True
            self._pending_onhook_at = None
            self._last_flash_at = None
            return ["on_hook"]
        return []


class PhoneIO:
    """Common phone I/O surface used by the state loop."""

    def is_off_hook(self) -> bool:
        raise NotImplementedError

    def ring_start(self) -> None:
        raise NotImplementedError

    def ring_stop(self) -> None:
        raise NotImplementedError

    def ring_pattern(self, bursts: list[tuple[int, int]]) -> None:
        """One-shot ring alert: list of (on_ms, off_ms). Default: continuous cadence via ring_start."""
        self.ring_start()

    def on_pulse(self, callback: PulseCallback) -> None:
        raise NotImplementedError

    def on_hook_change(self, callback: HookCallback) -> None:
        raise NotImplementedError

    def close(self) -> None:
        return


def attach_hook_cutoff(
    phone: PhoneIO,
    audio: "AudioRouter",
    *,
    on_hangup: Callable[[], None] | None = None,
) -> None:
    """Hook switch = hardware kill. Hangup stops all audio immediately.

    Wire this for every path that owns a handset (run loop, tune, diagnostics)
    unless the command explicitly documents that it ignores the hook.
    """
    from operator_os.audio import AudioRouter

    assert isinstance(audio, AudioRouter)

    def _on_hook(off_hook: bool) -> None:
        if off_hook:
            audio.set_hook(True)
            return
        audio.notify_hangup()
        if on_hangup is not None:
            on_hangup()

    phone.on_hook_change(_on_hook)
    # Sync audio to current cradle state (default AudioRouter is on-hook).
    if phone.is_off_hook():
        audio.set_hook(True)
    else:
        audio.notify_hangup()


def wait_off_hook(
    phone: PhoneIO,
    audio: "AudioRouter | None" = None,
    *,
    prompt: str = "Lift handset…",
    allow_enter: bool = True,
) -> bool:
    """Block until the handset is off-hook (hook is the power switch).

    Returns True if GPIO reported off-hook, False if the user pressed Enter to
    continue (benches only — GPIO may still be wrong).
    """
    if phone.is_off_hook():
        print("handset off-hook — continuing", flush=True)
        if audio is not None:
            audio.set_hook(True)
        return True
    print(prompt, flush=True)
    if allow_enter:
        print("  (or press Enter if already off-hook and this is stuck)", flush=True)
    last_status = 0.0
    while True:
        if phone.is_off_hook():
            print("handset off-hook — continuing", flush=True)
            if audio is not None:
                audio.set_hook(True)
            return True
        now = time.monotonic()
        if now - last_status >= 1.0:
            print("  still on-hook…", flush=True)
            last_status = now
        if allow_enter and _stdin_enter_pressed():
            print("  Enter — continuing (forcing off-hook for this session)", flush=True)
            if audio is not None:
                audio.set_hook(True)
            return False
        time.sleep(0.05)


def _stdin_enter_pressed() -> bool:
    """Non-blocking check for Enter on stdin (TTY benches)."""
    import select
    import sys

    if not sys.stdin.isatty():
        return False
    try:
        ready, _, _ = select.select([sys.stdin], [], [], 0)
    except (OSError, ValueError):
        return False
    if not ready:
        return False
    line = sys.stdin.readline()
    return True  # any line / Enter


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

    def ring_pattern(self, bursts: list[tuple[int, int]]) -> None:
        """Simulator: mark ringing briefly (pattern timing not simulated)."""
        if self.off_hook:
            return
        self.ringing = True
        # Leave ringing True until ring_stop — tests / script can stop explicitly.
        # For SMS alert, main still owns the pickup window.
        if not bursts:
            return
        # Auto-clear after nominal pattern duration so sim doesn't stick.
        total_ms = sum(max(0, int(on)) + max(0, int(off)) for on, off in bursts)

        def _clear() -> None:
            time.sleep(max(0.05, total_ms / 1000.0))
            if self.ringing:
                self.ringing = False

        threading.Thread(target=_clear, daemon=True).start()

    def on_pulse(self, callback: PulseCallback) -> None:
        self._pulse_cb = callback

    def on_hook_change(self, callback: HookCallback) -> None:
        self._hook_cb = callback


@dataclass
class GpioPhone(PhoneIO):
    """Rev A GPIO: hook BCM17, dial BCM10 when_pressed, ring BCM22."""

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

    def ring_pattern(self, bursts: list[tuple[int, int]]) -> None:
        """One-shot alert (e.g. SMS double-ring). Does not loop."""
        if self.is_off_hook() or not bursts:
            return
        self._ring_stop.clear()
        if self._ring_thread and self._ring_thread.is_alive():
            self.ring_stop()
            # Brief yield so the prior loop can exit before we reuse the pin.
            time.sleep(0.05)
        self._ring_stop.clear()
        pattern = [(max(0, int(on)), max(0, int(off))) for on, off in bursts]
        self._ring_thread = threading.Thread(
            target=self._ring_pattern_loop,
            args=(pattern,),
            daemon=True,
        )
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
        # Do not ring_stop here: ring HV can glitch the hook line and abort the
        # first cadence. Cutoff is debounced in _ring_loop; answer/hangup FSM
        # also issues ring_stop.
        if self._hook_cb:
            self._hook_cb(True)

    def _on_hook_released(self) -> None:
        if self._hook_cb:
            self._hook_cb(False)

    def _ring_loop(self) -> None:
        on_ms = self.profile.ring.cadence_on_ms
        off_ms = self.profile.ring.cadence_off_ms
        poll = self.profile.ring.poll_hook_while_ringing_ms / 1000.0
        # Sustained off-hook before cutoff (hook GPIO is noisy while ringing).
        off_hook_need_s = max(0.1, self.profile.ring.poll_hook_while_ringing_ms * 2 / 1000.0)
        off_hook_since: float | None = None
        while not self._ring_stop.is_set():
            if self.is_off_hook():
                if off_hook_since is None:
                    off_hook_since = time.monotonic()
                elif time.monotonic() - off_hook_since >= off_hook_need_s:
                    self.ring_stop()
                    return
            else:
                off_hook_since = None
            self._ring.on()  # type: ignore[union-attr]
            deadline = time.monotonic() + on_ms / 1000.0
            while time.monotonic() < deadline:
                if self._ring_stop.is_set():
                    self.ring_stop()
                    return
                if self.is_off_hook():
                    if off_hook_since is None:
                        off_hook_since = time.monotonic()
                    elif time.monotonic() - off_hook_since >= off_hook_need_s:
                        self.ring_stop()
                        return
                else:
                    off_hook_since = None
                time.sleep(poll)
            self._ring.off()  # type: ignore[union-attr]
            deadline = time.monotonic() + off_ms / 1000.0
            while time.monotonic() < deadline:
                if self._ring_stop.is_set():
                    self.ring_stop()
                    return
                if self.is_off_hook():
                    if off_hook_since is None:
                        off_hook_since = time.monotonic()
                    elif time.monotonic() - off_hook_since >= off_hook_need_s:
                        self.ring_stop()
                        return
                else:
                    off_hook_since = None
                time.sleep(poll)

    def _ring_pattern_loop(self, bursts: list[tuple[int, int]]) -> None:
        """Play ``bursts`` once then stop (SMS-style alert)."""
        poll = self.profile.ring.poll_hook_while_ringing_ms / 1000.0
        off_hook_need_s = max(0.1, self.profile.ring.poll_hook_while_ringing_ms * 2 / 1000.0)
        off_hook_since: float | None = None
        for on_ms, off_ms in bursts:
            if self._ring_stop.is_set():
                break
            if on_ms > 0:
                self._ring.on()  # type: ignore[union-attr]
                deadline = time.monotonic() + on_ms / 1000.0
                while time.monotonic() < deadline:
                    if self._ring_stop.is_set():
                        self.ring_stop()
                        return
                    if self.is_off_hook():
                        if off_hook_since is None:
                            off_hook_since = time.monotonic()
                        elif time.monotonic() - off_hook_since >= off_hook_need_s:
                            self.ring_stop()
                            return
                    else:
                        off_hook_since = None
                    time.sleep(poll)
                self._ring.off()  # type: ignore[union-attr]
            if off_ms <= 0 or self._ring_stop.is_set():
                continue
            deadline = time.monotonic() + off_ms / 1000.0
            while time.monotonic() < deadline:
                if self._ring_stop.is_set():
                    self.ring_stop()
                    return
                if self.is_off_hook():
                    if off_hook_since is None:
                        off_hook_since = time.monotonic()
                    elif time.monotonic() - off_hook_since >= off_hook_need_s:
                        self.ring_stop()
                        return
                else:
                    off_hook_since = None
                time.sleep(poll)
        self.ring_stop()
