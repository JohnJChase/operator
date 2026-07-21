"""CLI hardware diagnostics. Opt-in; never required for simulator tests."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from operator_os.audio import AudioRouter
from operator_os.config import HardwareProfile, load_profile
from operator_os.phone import GpioPhone


def trace_hook(profile: HardwareProfile, seconds: float = 30.0) -> int:
    phone = GpioPhone(profile)
    print(f"Tracing hook on GPIO{profile.gpio.hook_bcm} for {seconds:.0f}s (Ctrl+C to stop)")
    last = None
    try:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            off = phone.is_off_hook()
            if off != last:
                print(f"{time.strftime('%H:%M:%S')} {'OFF_HOOK' if off else 'ON_HOOK'}")
                last = off
            time.sleep(0.05)
    except KeyboardInterrupt:
        print()
    finally:
        phone.close()
    return 0


def trace_dial(profile: HardwareProfile, seconds: float = 60.0) -> int:
    phone = GpioPhone(profile)
    print(f"Tracing dial on GPIO{profile.gpio.dial_pulse_bcm} for {seconds:.0f}s")
    print("Dial digits; silence commits after digit_done_ms.")

    def on_pulse() -> None:
        print(f"  pulse #{phone.decoder.pending_pulses}")

    phone.on_pulse(on_pulse)
    try:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            digit = phone.decoder.poll(time.monotonic() * 1000)
            if digit is not None:
                print(f"DIGIT {digit}")
            time.sleep(0.02)
    except KeyboardInterrupt:
        print()
    finally:
        phone.close()
    return 0


def ring_test(profile: HardwareProfile, seconds: float = 2.0) -> int:
    phone = GpioPhone(profile)
    if phone.is_off_hook():
        print("Refusing ring test: handset is off-hook")
        phone.close()
        return 1
    print(f"Ringing GPIO{profile.gpio.ring_bcm} for up to {seconds:.1f}s (lift handset to cut off)")
    phone.ring_start()
    try:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if phone.is_off_hook():
                print("Off-hook detected; ring stopped")
                break
            time.sleep(0.02)
    finally:
        phone.ring_stop()
        phone.close()
    print("Ring test done")
    return 0


def audio_test(profile: HardwareProfile, hz: float = 440.0, seconds: float = 2.0) -> int:
    audio = AudioRouter(profile.audio)
    audio.set_hook(off_hook=True)
    print(f"Playing {hz} Hz for {seconds:.1f}s on {profile.audio.alsa_device}")
    audio.play_tone(hz, seconds=seconds, wait=True)
    audio.stop()
    return 0


def mic_test(profile: HardwareProfile, seconds: float = 5.0) -> int:
    out = Path("data/recordings/mic-test.wav")
    audio = AudioRouter(profile.audio)
    audio.set_hook(off_hook=True)
    print(f"Recording {seconds:.0f}s to {out}")
    audio.record(seconds, out)
    print(f"Wrote {out} ({out.stat().st_size} bytes)")
    return 0


def selftest(profile: HardwareProfile, hardware: bool = False) -> int:
    print(f"profile: {profile.name}")
    print(f"hook GPIO{profile.gpio.hook_bcm}  dial GPIO{profile.gpio.dial_pulse_bcm}  "
          f"ring GPIO{profile.gpio.ring_bcm}")
    print(f"audio: {profile.audio.alsa_device} {profile.audio.sample_rate_hz}Hz")

    # Always-safe software checks
    from operator_os.dial import DialDecoder, pulses_to_digit
    from operator_os.state import Event, PhoneController, State

    assert pulses_to_digit(10) == 0
    assert pulses_to_digit(2) == 2
    d = DialDecoder(digit_done_ms=100)
    d.pulse(0)
    d.pulse(50)
    assert d.poll(100) is None
    assert d.poll(200) == 2

    ctl = PhoneController()
    ctl.handle(Event("off_hook"))
    assert ctl.state == State.DIAL_TONE
    ctl.handle(Event("hangup"))
    assert ctl.state == State.ON_HOOK_IDLE
    print("software selftest: ok")

    if not hardware:
        return 0

    phone = GpioPhone(profile)
    try:
        print(f"hook now: {'OFF_HOOK' if phone.is_off_hook() else 'ON_HOOK'}")
        phone.ring_stop()
        print("ring GPIO forced off at selftest")
    finally:
        phone.close()

    audio = AudioRouter(profile.audio)
    audio.set_hook(off_hook=True)
    audio.play_tone(440, seconds=0.5, wait=True)
    print("440 Hz tone played")
    return 0


def load_or_exit(path: str | None) -> HardwareProfile:
    try:
        return load_profile(path)
    except FileNotFoundError:
        print(f"hardware profile not found: {path or 'config/hardware_profile.yaml'}", file=sys.stderr)
        raise SystemExit(2) from None
