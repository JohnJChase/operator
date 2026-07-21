"""CLI hardware diagnostics. Opt-in; never required for simulator tests."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from operator_os.audio import AudioRouter
from operator_os.config import HardwareProfile
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
    from operator_os.phone import GpioPhone, attach_hook_cutoff, wait_off_hook

    audio = AudioRouter(profile.audio)
    phone = GpioPhone(profile)
    attach_hook_cutoff(phone, audio)
    wait_off_hook(phone, audio, prompt="Lift handset for audio test…")
    print(f"Playing {hz} Hz for {seconds:.1f}s on {profile.audio.alsa_device} (hang up to cut)")
    audio.play_tone(hz, seconds=seconds, wait=True)
    phone.close()
    audio.close()
    return 0


def mic_test(profile: HardwareProfile, seconds: float = 5.0) -> int:
    from operator_os.audio import analyze_wav_levels
    from operator_os.phone import GpioPhone, attach_hook_cutoff, wait_off_hook

    out = Path("data/recordings/mic-test.wav")
    audio = AudioRouter(profile.audio)
    phone = GpioPhone(profile)
    attach_hook_cutoff(phone, audio)
    wait_off_hook(phone, audio, prompt="Lift handset for mic test…")
    print(f"Recording {seconds:.0f}s to {out} (hang up to abort)")
    try:
        audio.record(seconds, out)
    except RuntimeError as e:
        print(f"aborted: {e}")
        phone.close()
        audio.close()
        return 1
    if audio.is_on_hook or not out.is_file():
        print("hangup — no recording kept")
        phone.close()
        audio.close()
        return 1
    levels = analyze_wav_levels(out)
    print(
        f"{levels.duration_s:.2f}s {levels.sample_rate_hz} Hz  "
        f"peak={levels.peak_dbfs:.1f} dBFS  rms={levels.rms_dbfs:.1f} dBFS  "
        f"clips={levels.clip_samples}"
    )
    print(f"verdict: {levels.verdict} — {levels.detail}")
    phone.close()
    audio.close()
    return 0 if levels.ok else 1


def speak_test(profile: HardwareProfile, text: str = "This is the operator.") -> int:
    from operator_os.phone import GpioPhone, attach_hook_cutoff, wait_off_hook

    audio = AudioRouter(profile.audio)
    phone = GpioPhone(profile)
    attach_hook_cutoff(phone, audio)
    wait_off_hook(phone, audio, prompt="Lift handset for speak test…")
    print(f"tts engine={audio.engine}")
    print(f"voice={profile.audio.piper_voice}")
    print(f"model={audio.model_path}")
    if audio.engine != "piper":
        print("WARNING: Piper not loaded; using espeak fallback", file=sys.stderr)
    audio.speak(text)
    phone.close()
    audio.close()
    return 0 if audio.engine == "piper" else 1


def crossbar_test(profile: HardwareProfile) -> int:
    """Play the outside-line seize effect once (click → silence → dial tone)."""
    from operator_os.phone import GpioPhone, attach_hook_cutoff, wait_off_hook

    audio = AudioRouter(profile.audio)
    phone = GpioPhone(profile)
    attach_hook_cutoff(phone, audio)
    wait_off_hook(phone, audio, prompt="Lift handset for crossbar test…")
    print("crossbar seize: click/thud → blind spot → external dial tone (hang up to cut)")
    audio.seize_outside_line()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not audio.is_on_hook:
        time.sleep(0.05)
    phone.close()
    audio.close()
    print("done")
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

    rc = 0
    phone: GpioPhone | None = None
    try:
        phone = GpioPhone(profile)
        # Fail-off: ring must be low at open.
        phone.ring_stop()
        hook = "OFF_HOOK" if phone.is_off_hook() else "ON_HOOK"
        print(f"hook: {hook}")
        print(f"dial: GPIO{profile.gpio.dial_pulse_bcm} ready (pulse-only)")
        print(f"ring: GPIO{profile.gpio.ring_bcm} forced OFF (fail-safe)")
    except Exception as e:
        print(f"hook/dial/ring: FAIL ({e})", file=sys.stderr)
        rc = 1
    finally:
        if phone is not None:
            phone.close()

    try:
        audio = AudioRouter(profile.audio)
        print(f"tts: {audio.engine} model={audio.model_path}")
        from operator_os.phone import attach_hook_cutoff, wait_off_hook

        assert phone is not None
        # Re-open phone for audio (closed above) — keep fail-safe ring off.
        phone = GpioPhone(profile)
        attach_hook_cutoff(phone, audio)
        wait_off_hook(phone, audio, prompt="Lift handset for selftest audio…")
        audio.play_tone(440, seconds=0.3, wait=True)
        print("audio playback: ok (440 Hz)")
        # Short mic capture + level check (no secret paths printed beyond data/).
        out = Path("data/recordings/selftest-mic.wav")
        audio.record(1.0, out)
        from operator_os.audio import analyze_wav_levels

        levels = analyze_wav_levels(out)
        print(
            f"mic: peak={levels.peak_dbfs:.1f} dBFS rms={levels.rms_dbfs:.1f} dBFS "
            f"verdict={levels.verdict}"
        )
        if levels.verdict == "BAD":
            rc = 1
        audio.close()
        phone.close()
        phone = None
    except Exception as e:
        print(f"audio/mic: FAIL ({e})", file=sys.stderr)
        rc = 1
        try:
            if phone is not None:
                phone.close()
        except Exception:
            pass

    print("hardware selftest: ok" if rc == 0 else "hardware selftest: FAILED")
    return rc


def print_status(profile: HardwareProfile) -> int:
    """CLI status: profile, caches, GPIO snapshot — no secrets."""
    print(f"profile={profile.name}")
    print(
        f"gpio hook={profile.gpio.hook_bcm} dial={profile.gpio.dial_pulse_bcm} "
        f"ring={profile.gpio.ring_bcm}"
    )
    print(f"audio device={profile.audio.alsa_device} rate={profile.audio.sample_rate_hz}")

    audio = AudioRouter(profile.audio)
    print(f"tts engine={audio.engine} voice={profile.audio.piper_voice}")
    print(f"tts model={audio.model_path}")
    audio.close()

    for label, path in (
        ("weather.json", Path("data/weather.json")),
        ("weather.wav", Path("data/weather.wav")),
        ("news.json", Path("data/news.json")),
        ("news.wav", Path("data/news.wav")),
        ("events.jsonl", Path("data/events.jsonl")),
    ):
        if path.is_file():
            print(f"cache {label}: {path.stat().st_size} bytes")
        else:
            print(f"cache {label}: missing")

    try:
        phone = GpioPhone(profile)
        try:
            phone.ring_stop()
            print(f"hook_now={'OFF_HOOK' if phone.is_off_hook() else 'ON_HOOK'}")
            print("ring_now=OFF")
        finally:
            phone.close()
    except Exception as e:
        print(f"gpio_snapshot: unavailable ({e})")

    print("secrets: not shown")
    return 0
