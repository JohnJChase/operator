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
    print_status,
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
    ot = sub.add_parser("operator-test", help="Realtime operator smoke (WS session)")
    ot.add_argument("--text", default="What is the weather?")
    sub.add_parser(
        "realtime-tune",
        help="interactive Realtime mic/VAD tuning bench (live RMS + knobs)",
    )
    sub.add_parser(
        "realtime-autotune",
        help="guided Realtime autotune (scripted prompts + mic/response measure)",
    )
    sub.add_parser("status", help="print profile and current state summary")
    ref = sub.add_parser("refresh", help="fetch and cache news/weather")
    ref.add_argument("--weather", action="store_true", help="refresh weather only")
    ref.add_argument("--news", action="store_true", help="refresh news only")

    args = parser.parse_args(argv)
    from operator_os.refresh import load_dotenv

    load_dotenv()
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
    if args.cmd == "operator-test":
        from operator_os.openai_client import api_key_from_env
        from operator_os.realtime_operator import UNAVAILABLE, realtime_text_smoke

        key = api_key_from_env()
        if not key:
            print(UNAVAILABLE, file=sys.stderr)
            raise SystemExit(1)
        try:
            reply = realtime_text_smoke(key, args.text, profile=profile)
            print(f"operator: {reply}")
            raise SystemExit(0 if reply else 1)
        except Exception as e:
            print(f"operator-test failed: {e}", file=sys.stderr)
            raise SystemExit(1) from e
    if args.cmd == "realtime-tune":
        from operator_os.realtime_tune import run_realtime_tune

        raise SystemExit(run_realtime_tune(profile, profile_path=args.config))
    if args.cmd == "realtime-autotune":
        from operator_os.realtime_autotune import run_realtime_autotune

        raise SystemExit(run_realtime_autotune(profile, profile_path=args.config))
    if args.cmd == "refresh":
        from operator_os.refresh import refresh_all

        both = not args.weather and not args.news
        raise SystemExit(
            refresh_all(profile, weather=both or args.weather, news=both or args.news)
        )
    if args.cmd == "status":
        raise SystemExit(print_status(profile))
    if args.cmd == "simulate":
        phone = SimulatorPhone(profile)
        audio = AudioRouter(profile.audio)
        if args.script:
            raise SystemExit(_run_script(phone, audio, events, args.script))
        raise SystemExit(_simulate_repl(phone, audio, events))
    if args.cmd == "run":
        phone = GpioPhone(profile)
        phone.ring_stop()  # fail-off before entering the loop
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
    """Main telephone loop.

    Hook is a hardware cutoff (highest priority, every path):
      1) GPIO hangup → audio.notify_hangup() (marks on-hook + kills aplay/arecord)
         and RealtimeSession.cancel_now() if live
      2) Hook events drained before pulses every loop tick
      3) AudioRouter refuses new playback while on-hook
      4) Service playback is non-blocking so the loop can always see hangup
    Pulse callbacks only enqueue; they never touch audio.
    """
    import queue

    ctl = PhoneController()
    decoder = phone.decoder  # type: ignore[attr-defined]
    stop_at = time.monotonic() + max_seconds if max_seconds else None
    q: queue.SimpleQueue[tuple] = queue.SimpleQueue()
    # True while PLAYING_SERVICE audio was started non-blocking; hangup clears it.
    await_service_done = False
    op_session = None  # RealtimeSession | None

    def _status(msg: str) -> None:
        print(msg, flush=True)

    def apply(tr) -> None:
        nonlocal await_service_done, op_session
        for action in tr.actions:
            _do_action(action, phone, audio, events, ctl, live_audio)
        if tr.reason:
            _status(f"state={ctl.state.value}  ({tr.reason})")
        if ctl.state == State.ON_HOOK_IDLE:
            await_service_done = False
            if op_session is not None:
                op_session.cancel_now()
                op_session = None

    def on_hook_isr(off_hook: bool) -> None:
        nonlocal await_service_done, op_session
        # Interrupt path: silence first, then ask main loop for state change.
        if not off_hook:
            await_service_done = False  # don't treat kill as "service finished"
            if op_session is not None:
                op_session.cancel_now()
            audio.notify_hangup()
        q.put(("hook", off_hook))

    phone.on_hook_change(on_hook_isr)
    phone.on_pulse(lambda: q.put(("pulse", decoder.pending_pulses)))

    if phone.is_off_hook() and ctl.state == State.ON_HOOK_IDLE:
        q.put(("hook", True))

    _status(f"state={ctl.state.value}  (lift handset; Ctrl+C to quit)")
    try:
        while True:
            if stop_at and time.monotonic() >= stop_at:
                return 0

            hooks, pulses = _drain_prioritized(q)
            for off_hook in hooks:
                if off_hook:
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
                    await_service_done = False
                    if op_session is not None:
                        op_session.cancel_now()
                        op_session = None
            # If we went on-hook, drop pending pulses from this tick.
            if ctl.state == State.ON_HOOK_IDLE:
                pulses = []
                decoder.reset()

            for pending in pulses:
                if ctl.state == State.DIAL_TONE:
                    apply(ctl.handle(Event("pulse")))
                elif ctl.state not in (State.DIAL_TONE, State.COLLECTING_DIGIT):
                    decoder.reset()
                    continue
                _status(f"pulse #{pending}")

            # Playback / operator session ended while still off-hook → dial tone.
            # Hangup kills aplay too; never treat that as service_done (dial-tone blip).
            if ctl.state == State.PLAYING_SERVICE and await_service_done and phone.is_off_hook():
                op_alive = op_session is not None and op_session.is_alive()
                if op_alive:
                    time.sleep(0.02)
                    continue
                if op_session is not None:
                    op_session = None
                if not audio.is_busy():
                    await_service_done = False
                    decoder.reset()
                    _flush_queue_pulses(q)
                    apply(ctl.handle(Event("service_done")))

            now_ms = time.monotonic() * 1000
            if ctl.state not in (State.DIAL_TONE, State.COLLECTING_DIGIT):
                time.sleep(0.02)
                continue

            digit = decoder.poll(now_ms)
            if digit is not None:
                pulses_n = 10 if digit == 0 else digit
                events.emit("digit", value=digit, pulses=pulses_n)
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
                await_service_done, op_session = _play_service(
                    result, audio, events, live_audio, profile=profile
                )
                decoder.reset()
                _flush_queue_pulses(q)
            time.sleep(0.02)
    except KeyboardInterrupt:
        _status("quit")
        apply(ctl.handle(Event("hangup")))
        return 0


def _drain_prioritized(q) -> tuple[list[bool], list[int]]:
    """Drain the event queue; hooks are returned first (interrupt priority)."""
    import queue as _queue

    hooks: list[bool] = []
    pulses: list[int] = []
    while True:
        try:
            kind, value = q.get_nowait()
        except _queue.Empty:
            break
        if kind == "hook":
            hooks.append(bool(value))
        elif kind == "pulse":
            pulses.append(int(value))
    return hooks, pulses


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
        # Hangup lands in ON_HOOK_IDLE first — that path is the hardware cutoff.
        # Other audio_stop (kill dial tone for a digit) must stay off-hook.
        if ctl.state == State.ON_HOOK_IDLE:
            audio.notify_hangup()
        else:
            audio.stop()
    elif action == "ring_stop":
        phone.ring_stop()
    elif action == "ring_start":
        phone.ring_start()
    elif action.startswith("fx_") or action == "crossbar":
        # Line-plant signatures declared on the transition chart.
        if live_audio and phone.is_off_hook():
            audio.play_plant(action, wait=True)
            events.emit("audio", value=action)
    elif action == "dial_tone":
        # Never start dial tone on-hook (hangup must not blip tone).
        if live_audio and phone.is_off_hook():
            audio.set_hook(True)
            audio.play_tone("dial", wait=False)
            events.emit("audio", value="dial_tone")
    elif action == "play_service":
        pass  # handled after digit via _play_service
    if action in ("audio_stop", "ring_stop") and ctl.state.value == "ON_HOOK_IDLE":
        if hasattr(phone, "decoder"):
            phone.decoder.reset()


def _play_service(
    result: ServiceResult,
    audio: AudioRouter,
    events: EventLog,
    live: bool,
    *,
    profile: HardwareProfile | None = None,
) -> tuple[bool, object | None]:
    """Start service content only. Plant FX come from the transition chart.

    Live playback must not block — hangup is processed on the main loop and must
    preempt news/weather/announcements immediately.
    """
    events.emit("service", digit=result.digit, kind=result.kind)
    print(f"service digit={result.digit} kind={result.kind}: {result.text or result.path}", flush=True)
    if not live:
        return False, None
    if result.kind == "realtime_operator":
        from operator_os.realtime_operator import UNAVAILABLE, start_realtime

        if profile is None:
            audio.speak(UNAVAILABLE, wait=False)
            return True, None
        session = start_realtime(audio, events, profile=profile)
        if session is None:
            audio.speak(UNAVAILABLE, wait=False)
            return True, None
        return True, session
    if result.kind == "outside_seize":
        # fx_outside already ran from the transition; tone runs until hangup.
        return False, None
    if result.kind == "stream" and result.url:
        try:
            audio.play_stream(result.url, wait=False)
        except Exception as e:
            print(f"stream failed: {e}", flush=True)
            audio.speak("That station is temporarily unavailable.", wait=False)
        return True, None
    if result.kind == "play_file" and result.path:
        audio.play_file(result.path, wait=False)
        return True, None
    if result.kind == "effect_then_speak":
        audio.speak(result.text, wait=False)
        return True, None
    audio.speak(result.text, wait=False)
    return True, None


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
