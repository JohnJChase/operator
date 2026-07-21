"""CLI entry and telephone loop wiring."""

from __future__ import annotations

import argparse
import sys
import time
from operator_os.audio import AudioRouter
from operator_os.config import HardwareProfile, load_profile
from operator_os.diagnostics import (
    audio_test,
    mic_test,
    ring_test,
    selftest,
    trace_dial,
    trace_hook,
)
from operator_os.events import EventLog
from operator_os.phone import GpioPhone, PhoneIO, SimulatorPhone
from operator_os.services import ServiceResult, handle_digit
from operator_os.state import Event, PhoneController, State


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="operator-os", description="WE302 Operator OS")
    parser.add_argument(
        "--config",
        default="config/hardware_profile.yaml",
        help="hardware profile YAML",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("run", help="run on real GPIO + audio")
    sim = sub.add_parser("simulate", help="interactive simulator (no GPIO)")
    sim.add_argument(
        "--script",
        help="comma script: off,digit:1,hangup",
    )
    st = sub.add_parser("selftest", help="software (and optional hardware) selftest")
    st.add_argument("--hardware", action="store_true")

    sub.add_parser("trace-hook", help="print hook transitions")
    sub.add_parser("trace-dial", help="print dial pulses and digits")
    rt = sub.add_parser("ring-test", help="energize ring relay briefly")
    rt.add_argument("--seconds", type=float, default=2.0)
    at = sub.add_parser("audio-test", help="play a test tone")
    at.add_argument("--tone", type=float, default=440.0)
    at.add_argument("--seconds", type=float, default=2.0)
    mt = sub.add_parser("mic-test", help="record from handset mic")
    mt.add_argument("--seconds", type=float, default=5.0)
    sp = sub.add_parser("speak-test", help="speak a phrase (reports Piper vs espeak)")
    sp.add_argument("--text", default="This is the operator.")
    sub.add_parser("crossbar-test", help="play outside-line seize click + dial tone")
    sub.add_parser("status", help="print profile and current state summary")

    args = parser.parse_args(argv)
    profile = load_profile(args.config)
    events = EventLog()

    if args.cmd == "selftest":
        raise SystemExit(selftest(profile, hardware=args.hardware))
    if args.cmd == "trace-hook":
        raise SystemExit(trace_hook(profile))
    if args.cmd == "trace-dial":
        raise SystemExit(trace_dial(profile))
    if args.cmd == "ring-test":
        raise SystemExit(ring_test(profile, seconds=args.seconds))
    if args.cmd == "audio-test":
        raise SystemExit(audio_test(profile, hz=args.tone, seconds=args.seconds))
    if args.cmd == "mic-test":
        raise SystemExit(mic_test(profile, seconds=args.seconds))
    if args.cmd == "speak-test":
        from operator_os.diagnostics import speak_test

        raise SystemExit(speak_test(profile, text=args.text))
    if args.cmd == "crossbar-test":
        from operator_os.diagnostics import crossbar_test

        raise SystemExit(crossbar_test(profile))
    if args.cmd == "status":
        print(f"profile={profile.name}")
        print(f"hook={profile.gpio.hook_bcm} dial={profile.gpio.dial_pulse_bcm} "
              f"ring={profile.gpio.ring_bcm}")
        print(f"audio={profile.audio.alsa_device}")
        audio = AudioRouter(profile.audio)
        print(f"tts={audio.engine} voice={profile.audio.piper_voice} model={audio.model_path}")
        raise SystemExit(0)
    if args.cmd == "simulate":
        phone = SimulatorPhone(profile)
        audio = AudioRouter(profile.audio)
        if args.script:
            raise SystemExit(_run_script(phone, audio, events, args.script))
        raise SystemExit(_simulate_repl(phone, audio, events))
    if args.cmd == "run":
        phone = GpioPhone(profile)
        audio = AudioRouter(profile.audio)
        try:
            raise SystemExit(run_loop(phone, audio, events, profile, live_audio=True))
        finally:
            phone.close()
            audio.close()


def run_loop(
    phone: PhoneIO,
    audio: AudioRouter,
    events: EventLog,
    profile: HardwareProfile,
    *,
    live_audio: bool,
    max_seconds: float | None = None,
) -> int:
    """Main telephone loop. GPIO callbacks only enqueue; audio runs here."""
    import queue

    ctl = PhoneController()
    decoder = phone.decoder  # type: ignore[attr-defined]
    stop_at = time.monotonic() + max_seconds if max_seconds else None
    q: queue.SimpleQueue[tuple] = queue.SimpleQueue()

    def _status(msg: str) -> None:
        print(msg, flush=True)

    def apply(tr) -> None:
        for action in tr.actions:
            _do_action(action, phone, audio, events, ctl, live_audio)
        if tr.reason:
            _status(f"state={ctl.state.value}  ({tr.reason})")

    # GPIO/sim callbacks must not touch audio — stop() used to block pulse ISR.
    phone.on_hook_change(lambda off: q.put(("hook", off)))
    phone.on_pulse(lambda: q.put(("pulse", decoder.pending_pulses)))

    if phone.is_off_hook() and ctl.state == State.ON_HOOK_IDLE:
        q.put(("hook", True))

    _status(f"state={ctl.state.value}  (lift handset; Ctrl+C to quit)")
    try:
        while True:
            if stop_at and time.monotonic() >= stop_at:
                return 0

            while True:
                try:
                    kind, value = q.get_nowait()
                except queue.Empty:
                    break
                if kind == "hook":
                    if value:
                        events.emit("hook", value="off_hook")
                        _status("hook=OFF_HOOK")
                        apply(ctl.handle(Event("off_hook")))
                        audio.set_hook(True)
                    else:
                        events.emit("hook", value="on_hook")
                        _status("hook=ON_HOOK")
                        apply(ctl.handle(Event("on_hook")))
                        audio.set_hook(False)
                        decoder.reset()
                elif kind == "pulse":
                    if ctl.state == State.DIAL_TONE:
                        apply(ctl.handle(Event("pulse")))
                    elif ctl.state not in (State.DIAL_TONE, State.COLLECTING_DIGIT):
                        # Ignore dial chatter during announcements / outside tone.
                        decoder.reset()
                        continue
                    _status(f"pulse #{value}")

            now_ms = time.monotonic() * 1000
            if ctl.state not in (State.DIAL_TONE, State.COLLECTING_DIGIT):
                time.sleep(0.02)
                continue

            digit = decoder.poll(now_ms)
            if digit is not None:
                pulses = 10 if digit == 0 else digit
                events.emit("digit", value=digit, pulses=pulses)
                _status(f"digit={digit}")
                prev = ctl.state
                tr = ctl.handle(Event("digit", value=digit))
                events.emit(
                    "state",
                    **{"from": prev.value, "to": tr.state.value, "reason": tr.reason},
                )
                apply(tr)
                decoder.reset()
                result = handle_digit(digit)
                _play_service(result, audio, events, live_audio)
                # Dial activity during TTS must not become a digit after the announcement.
                decoder.reset()
                _flush_queue_pulses(q)
                # Outside-line seize leaves external dial tone running until hangup.
                if ctl.state == State.PLAYING_SERVICE and result.kind != "outside_seize":
                    apply(ctl.handle(Event("service_done")))
            time.sleep(0.02)
    except KeyboardInterrupt:
        _status("quit")
        apply(ctl.handle(Event("hangup")))
        return 0


def _flush_queue_pulses(q) -> None:
    """Drop queued pulse notifications; keep hook events."""
    import queue as _queue

    kept: list[tuple] = []
    while True:
        try:
            item = q.get_nowait()
        except _queue.Empty:
            break
        if item[0] != "pulse":
            kept.append(item)
    for item in kept:
        q.put(item)


def _do_action(action, phone, audio, events, ctl, live_audio) -> None:
    if action == "audio_stop":
        audio.stop()
    elif action == "ring_stop":
        phone.ring_stop()
    elif action == "ring_start":
        phone.ring_start()
    elif action == "dial_tone":
        if live_audio:
            audio.set_hook(True)
            audio.play_tone("dial", wait=False)
        events.emit("audio", value="dial_tone")
    elif action == "play_service":
        pass  # handled after digit via _play_service
    if action in ("audio_stop", "ring_stop") and ctl.state.value == "ON_HOOK_IDLE":
        if hasattr(phone, "decoder"):
            phone.decoder.reset()


def _play_service(result: ServiceResult, audio: AudioRouter, events: EventLog, live: bool) -> None:
    events.emit("service", digit=result.digit, kind=result.kind)
    print(f"service digit={result.digit} kind={result.kind}: {result.text or result.path}", flush=True)
    if not live:
        return
    if result.kind == "outside_seize":
        audio.seize_outside_line()
        return
    audio.stop()
    if result.kind == "play_file" and result.path:
        audio.play_file(result.path, wait=True)
    elif result.kind == "effect_then_speak":
        audio.play_tone("crossbar", seconds=0.4, wait=True)
        audio.speak(result.text)
    else:
        audio.speak(result.text)


def _run_script(phone: SimulatorPhone, audio: AudioRouter, events: EventLog, script: str) -> int:
    ctl = PhoneController()
    steps = [s.strip() for s in script.split(",") if s.strip()]
    now = 0.0
    print(f"script: {script}")
    for step in steps:
        if step in ("off", "off_hook"):
            phone.set_hook(True)
            tr = ctl.handle(Event("off_hook"))
            events.emit("hook", value="off_hook")
            events.emit("state", **{"from": "ON_HOOK_IDLE", "to": tr.state.value, "reason": tr.reason})
            print(f"-> {ctl.state.value}")
        elif step in ("on", "hangup", "on_hook"):
            phone.set_hook(False)
            tr = ctl.handle(Event("hangup"))
            audio.stop()
            events.emit("hook", value="on_hook")
            events.emit("state", **{"to": tr.state.value, "reason": "hangup"})
            print(f"-> {ctl.state.value}")
        elif step.startswith("digit:"):
            digit = int(step.split(":", 1)[1])
            phone.inject_digit(digit, now_ms=now)
            now += 50 * (10 if digit == 0 else digit)
            now += phone.profile.dial.digit_done_ms + 10
            got = phone.decoder.poll(now)
            assert got == digit, f"expected {digit}, got {got}"
            if ctl.state == State.DIAL_TONE:
                ctl.handle(Event("pulse"))
            tr = ctl.handle(Event("digit", value=digit))
            events.emit("digit", value=digit)
            events.emit("state", **{"to": tr.state.value, "reason": tr.reason})
            result = handle_digit(digit)
            _play_service(result, audio, events, live=False)
            if ctl.state == State.PLAYING_SERVICE and result.kind != "outside_seize":
                ctl.handle(Event("service_done"))
            print(f"-> digit {digit} => {ctl.state.value}")
        elif step.startswith("pulses:"):
            count = int(step.split(":", 1)[1])
            phone.inject_pulses(count, now_ms=now)
            now += 50 * count + phone.profile.dial.digit_done_ms + 10
            got = phone.decoder.poll(now)
            print(f"-> pulses {count} => digit {got}")
        else:
            print(f"unknown step: {step}", file=sys.stderr)
            return 2
    print("script ok")
    return 0


def _simulate_repl(phone: SimulatorPhone, audio: AudioRouter, events: EventLog) -> int:
    print("Simulator. Commands: off | on | digit N | ring | hangup | quit")
    ctl = PhoneController()
    now = 1000.0

    while True:
        try:
            line = input(f"[{ctl.state.value}]> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line in ("q", "quit", "exit"):
            return 0
        if line in ("off", "off_hook"):
            phone.set_hook(True)
            ctl.handle(Event("off_hook"))
            events.emit("hook", value="off_hook")
            continue
        if line in ("on", "hangup", "on_hook"):
            phone.set_hook(False)
            ctl.handle(Event("hangup"))
            audio.stop()
            events.emit("hook", value="on_hook")
            continue
        if line.startswith("digit"):
            parts = line.split()
            if len(parts) != 2:
                print("usage: digit N")
                continue
            digit = int(parts[1])
            phone.inject_digit(digit, now_ms=now)
            now += 50 * (10 if digit == 0 else digit) + phone.profile.dial.digit_done_ms + 10
            got = phone.decoder.poll(now)
            if got is None:
                print("no digit decoded")
                continue
            if ctl.state == State.DIAL_TONE:
                ctl.handle(Event("pulse"))
            tr = ctl.handle(Event("digit", value=got))
            events.emit("digit", value=got)
            result = handle_digit(got)
            _play_service(result, audio, events, live=False)
            if ctl.state == State.PLAYING_SERVICE and result.kind != "outside_seize":
                ctl.handle(Event("service_done"))
            print(f"digit {got} -> {tr.state.value}")
            continue
        if line == "ring":
            phone.ring_start()
            ctl.handle(Event("ring_start"))
            continue
        print("unknown command")


if __name__ == "__main__":
    main()
