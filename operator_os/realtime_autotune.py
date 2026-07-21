"""Guided Realtime autotune: scripted prompts + mic/response measurements."""

from __future__ import annotations

import statistics
import sys
import time
from dataclasses import dataclass
from operator_os.audio import AudioRouter
from operator_os.config import HardwareProfile
from operator_os.events import EventLog
from operator_os.local_tools import LocalTools, build_status_snapshot
from operator_os.openai_client import api_key_from_env
from operator_os.realtime_operator import UNAVAILABLE, RealtimeSession
from operator_os.refresh import load_dotenv

# --- pure suggestions (unit-tested) -----------------------------------------


def suggest_gate(floor_dbfs: float, *, margin_db: float = 6.0) -> float:
    """Gate just above idle floor so hiss stays closed.

    floor=-48 → gate≈-42; floor=-23 → gate≈-17. Never place the gate
    *below* the floor (that kept the door permanently open).
    """
    return float(round(max(-55.0, min(-12.0, floor_dbfs + margin_db))))


def suggest_capture_gain(
    speech_peak_dbfs: float,
    gate_dbfs: float,
    current_gain: float,
    *,
    headroom_db: float = 10.0,
) -> float:
    """Raise mic gain until speech peak clears the gate by headroom_db."""
    need = gate_dbfs + headroom_db
    if speech_peak_dbfs >= need:
        return round(current_gain, 2)
    # Each +6 dB ≈ ×2 linear. Deficit in dB → multiply gain.
    deficit = need - speech_peak_dbfs
    factor = 10.0 ** (deficit / 20.0)
    return round(max(0.5, min(12.0, current_gain * factor)), 2)


def suggest_echo_guard_ms(
    bleed_peak_dbfs: float,
    gate_dbfs: float,
    current_ms: float,
    *,
    open_fraction: float,
) -> float:
    """Lengthen echo guard if post-speech bleed still opens the door."""
    if open_fraction < 0.05 and bleed_peak_dbfs < gate_dbfs:
        return float(int(current_ms))
    bump = 400.0 if open_fraction >= 0.15 else 200.0
    return float(min(2500, int(current_ms + bump)))


def suggest_playback_gain(
    bleed_peak_dbfs: float,
    gate_dbfs: float,
    current: float,
) -> float:
    """Lower ear gain if bleed peaks near/above the gate."""
    if bleed_peak_dbfs < gate_dbfs - 3:
        return round(current, 2)
    return round(max(0.1, min(1.0, current * 0.75)), 2)


def suggest_vad_threshold(
    false_responses: int,
    missed_response: bool,
    current: float,
) -> float:
    if false_responses > 0:
        return round(min(0.95, current + 0.05 * false_responses), 2)
    if missed_response:
        return round(max(0.35, current - 0.1), 2)
    return round(current, 2)


@dataclass
class SampleWindow:
    rms: list[float]
    uplink_frac: float
    peak: float
    p90: float


def _sample(session: RealtimeSession, seconds: float, *, hz: float = 20.0) -> SampleWindow:
    n = max(1, int(seconds * hz))
    dt = 1.0 / hz
    levels: list[float] = []
    open_n = 0
    for _ in range(n):
        levels.append(session.last_rms_dbfs)
        if session.uplink_open:
            open_n += 1
        time.sleep(dt)
    peak = max(levels) if levels else -120.0
    p90 = float(statistics.quantiles(levels, n=10)[8]) if len(levels) >= 10 else peak
    return SampleWindow(rms=levels, uplink_frac=open_n / n, peak=peak, p90=p90)


def _wait_until(pred, *, timeout: float, label: str) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    print(f"  (timeout waiting for {label})", flush=True)
    return False


def _pause(msg: str) -> None:
    print(flush=True)
    print(msg, flush=True)
    try:
        input("  [Enter] when ready… ")
    except EOFError:
        time.sleep(1.0)


def _countdown(seconds: int, label: str) -> None:
    for i in range(seconds, 0, -1):
        print(f"  {label}… {i}  ", end="\r", flush=True)
        time.sleep(1.0)
    print(f"  {label}… go          ", flush=True)


def run_realtime_autotune(
    profile: HardwareProfile, *, profile_path: str = "config/hardware_profile.yaml"
) -> int:
    load_dotenv()
    key = api_key_from_env()
    if not key:
        print(UNAVAILABLE, file=sys.stderr)
        return 1

    print("WE302 Realtime autotune", flush=True)
    print("Hang up anytime to abort. Follow the short script.", flush=True)
    print("Barge-in disabled for this run so bleed cannot interrupt.", flush=True)

    from operator_os.phone import GpioPhone, attach_hook_cutoff, wait_off_hook

    # Hook first (before slow Piper load) so "Lift handset" is immediate.
    phone = GpioPhone(profile)
    wait_off_hook(phone)

    audio = AudioRouter(profile.audio)
    events = EventLog()
    tools = LocalTools(
        audio=audio,
        profile=profile,
        status_snapshot=build_status_snapshot(profile.name),
        voice_mode=True,
    )
    session = RealtimeSession(
        audio=audio,
        events=events,
        tools=tools,
        api_key=key,
        realtime_cfg=dict(profile.raw.get("realtime") or {}),
        auto_greet=False,
    )
    attach_hook_cutoff(phone, audio, on_hangup=session.cancel_now)
    audio.set_hook(True)
    session.start()

    if not session.wait_ready(10.0) or audio.is_on_hook:
        if audio.is_on_hook:
            phone.close()
            audio.close()
            return 0
        print("session failed to become ready", file=sys.stderr)
        session.cancel_now()
        phone.close()
        audio.close()
        return 1

    # Stable starting point for measurement.
    notes: list[str] = []

    try:
        _pause(
            '1) Handset to your ear, then press Enter.\n'
            '   You should hear: “Operator.”'
        )
        if audio.is_on_hook:
            return 0
        print("  → greeting…", flush=True)
        before_play = session._last_playback_at
        session.greet()
        _wait_until(
            lambda: session._last_playback_at > before_play or session.audio.is_on_hook,
            timeout=8.0,
            label="greeting audio",
        )
        _wait_until(
            lambda: session.listening and not session.echo_guarding,
            timeout=20.0,
            label="listening after greeting",
        )
        print("  → greeting done; stay silent", flush=True)
        time.sleep(0.3)
        session.clear_input()
        before_resp = session.response_count
        before_speech = session.speech_started_count

        print("\n2) SILENCE — do not talk (measuring noise floor).", flush=True)
        _countdown(2, "silence starts in")
        if audio.is_on_hook:
            return 0
        silence = _sample(session, 5.0)
        false_resp = max(0, session.response_count - before_resp)
        false_speech = max(0, session.speech_started_count - before_speech)
        gate = suggest_gate(silence.p90)
        session.update_cfg(mic_gate_dbfs=gate)
        notes.append(
            f"silence p90={silence.p90:+.1f}dB peak={silence.peak:+.1f}dB "
            f"uplink={silence.uplink_frac:.0%} → gate={gate:+.0f}"
        )
        if false_resp or false_speech:
            notes.append(
                f"false activity during silence: responses+{false_resp} "
                f"speech_started+{false_speech}"
            )
        print(f"  → gate set to {gate:+.0f} dBFS", flush=True)

        # Re-check silence with new gate.
        session.clear_input()
        time.sleep(0.2)
        before_resp = session.response_count
        silence2 = _sample(session, 3.0)
        if silence2.uplink_frac > 0.08:
            gate = suggest_gate(silence2.p90, margin_db=8.0)
            session.update_cfg(mic_gate_dbfs=gate)
            notes.append(f"silence still leaky; gate → {gate:+.0f}")
            print(f"  → gate raised to {gate:+.0f} (still opening on idle)", flush=True)

        _pause(
            '3) When prompted, say clearly once:\n'
            '     “What time is it?”\n'
            "   Then stop and wait for the operator."
        )
        if audio.is_on_hook:
            return 0
        before_resp = session.response_count
        print("\n4) SPEAK now — “What time is it?”", flush=True)
        _countdown(2, "speak in")
        speech = _sample(session, 4.0)
        gain = float(session.cfg.get("capture_gain", 8.0))
        new_gain = suggest_capture_gain(speech.peak, gate, gain)
        if new_gain != gain:
            session.update_cfg(capture_gain=new_gain)
            notes.append(
                f"speech peak={speech.peak:+.1f}dB vs gate={gate:+.0f} "
                f"→ mic gain {gain:.2f}→{new_gain:.2f}"
            )
            print(f"  → mic gain {gain:.2f} → {new_gain:.2f}", flush=True)
            # One more short speak if we had to boost.
            _pause('5) Mic was quiet — say again: “What time is it?”')
            if audio.is_on_hook:
                return 0
            print("\n   SPEAK now.", flush=True)
            _countdown(2, "speak in")
            speech = _sample(session, 4.0)
            before_resp = session.response_count

        notes.append(
            f"speech peak={speech.peak:+.1f}dB uplink={speech.uplink_frac:.0%}"
        )
        if speech.uplink_frac < 0.15:
            # Speech never opened the door — drop gate toward peak.
            gate = float(round(min(gate, speech.peak - 4)))
            session.update_cfg(mic_gate_dbfs=gate)
            notes.append(f"speech missed gate; gate → {gate:+.0f}")
            print(f"  → gate lowered to {gate:+.0f} (speech never opened door)", flush=True)

        print("\n5) Waiting for operator reply…", flush=True)
        got = _wait_until(
            lambda: session.response_count > before_resp or session._model_speaking,
            timeout=12.0,
            label="operator response",
        )
        missed = not got
        if got:
            print("  → heard a response starting", flush=True)
            _wait_until(
                lambda: session.listening and not session.echo_guarding,
                timeout=25.0,
                label="reply finished",
            )
        else:
            print("  → no response (will soften VAD)", flush=True)

        vad = float(session.cfg.get("vad_threshold", 0.75))
        new_vad = suggest_vad_threshold(false_resp + false_speech, missed, vad)
        if new_vad != vad:
            session.update_cfg(vad_threshold=new_vad)
            notes.append(f"vad {vad:.2f}→{new_vad:.2f}")
            print(f"  → VAD threshold {vad:.2f} → {new_vad:.2f}", flush=True)

        # Bleed / self-response after the reply.
        print("\n6) SILENCE again — checking earpiece bleed / self-talk.", flush=True)
        session.hush()
        _countdown(1, "silence")
        if audio.is_on_hook:
            return 0
        before_resp = session.response_count
        before_speech = session.speech_started_count
        # Sample during any remaining echo guard + settle window.
        bleed = _sample(session, 4.0)
        false_after = max(0, session.response_count - before_resp)
        speech_after = max(0, session.speech_started_count - before_speech)
        ear = float(session.cfg.get("playback_gain", 0.35))
        echo_ms = float(session.cfg.get("echo_guard_ms", 900))
        new_ear = suggest_playback_gain(bleed.peak, gate, ear)
        new_echo = suggest_echo_guard_ms(
            bleed.peak, gate, echo_ms, open_fraction=bleed.uplink_frac
        )
        if false_after or speech_after:
            new_ear = suggest_playback_gain(max(bleed.peak, gate), gate, new_ear)
            new_echo = min(2500, new_echo + 300)
            notes.append(
                f"self-talk after reply: responses+{false_after} "
                f"speech_started+{speech_after}"
            )
            print("  → detected possible self-response / bleed trigger", flush=True)
        if new_ear != ear:
            session.update_cfg(playback_gain=new_ear)
            notes.append(f"ear gain {ear:.2f}→{new_ear:.2f}")
            print(f"  → ear gain {ear:.2f} → {new_ear:.2f}", flush=True)
        if new_echo != echo_ms:
            session.update_cfg(echo_guard_ms=new_echo)
            notes.append(f"echo guard {echo_ms:.0f}→{new_echo:.0f}ms")
            print(f"  → echo guard {echo_ms:.0f} → {new_echo:.0f} ms", flush=True)
        notes.append(
            f"bleed peak={bleed.peak:+.1f}dB uplink={bleed.uplink_frac:.0%}"
        )

        # Restore listen; no barge in Piper-mouth mode.
        path = session.save_cfg(profile_path)
        print("\n=== Autotune result (saved) ===", flush=True)
        for k in (
            "capture_gain",
            "playback_gain",
            "mic_gate_dbfs",
            "echo_guard_ms",
            "vad_threshold",
            "mic_hangover_ms",
        ):
            print(f"  {k}: {session.cfg.get(k)}", flush=True)
        print(f"  wrote {path}", flush=True)
        print("\nNotes:", flush=True)
        for n in notes:
            print(f"  - {n}", flush=True)
        print(
            "\nOptional: `just realtime-tune` to eyeball the meter, "
            "or re-run autotune if it still answers silence.",
            flush=True,
        )
        return 0
    except KeyboardInterrupt:
        print("\naborted", flush=True)
        return 130
    finally:
        session.cancel_now()
        for _ in range(30):
            if not session.is_alive():
                break
            time.sleep(0.1)
        phone.close()
        audio.close()
