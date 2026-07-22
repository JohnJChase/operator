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
    ot = sub.add_parser("operator-test", help="info desk text smoke (no mic)")
    ot.add_argument("--text", default="What is the weather?")
    ca = sub.add_parser(
        "calendar-auth",
        help="OAuth: write GOOGLE_OAUTH_REFRESH_TOKEN to .env (digit 7)",
    )
    ca.add_argument(
        "--no-browser",
        action="store_true",
        help="print URL only; do not open a browser",
    )
    inj = sub.add_parser("sms-inject", help="POST a fake inbound SMS to local webhook")
    inj.add_argument("--from", dest="sms_from", required=True, help="E.164 or 10-digit from")
    inj.add_argument("--text", required=True, help="message body")
    inj.add_argument("--to", default="", help="destination (default TELNYX_SMS_FROM)")
    inj.add_argument(
        "--port",
        type=int,
        default=0,
        help="webhook port (default OPERATOR_SMS_WEBHOOK_PORT or 8787)",
    )
    snd = sub.add_parser("sms-send", help="send one outbound SMS via Telnyx")
    snd.add_argument("--to", required=True)
    snd.add_argument("--text", required=True)
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
        from operator_os.info_desk import UNAVAILABLE, info_desk_text_smoke
        from operator_os.openai_client import api_key_from_env

        key = api_key_from_env()
        if not key:
            print(UNAVAILABLE, file=sys.stderr)
            raise SystemExit(1)
        try:
            audio = AudioRouter(profile.audio)
            reply = info_desk_text_smoke(args.text, profile=profile, audio=audio)
            print(f"operator: {reply}")
            raise SystemExit(0 if reply else 1)
        except Exception as e:
            print(f"operator-test failed: {e}", file=sys.stderr)
            raise SystemExit(1) from e
    if args.cmd == "calendar-auth":
        from operator_os.google_calendar import run_calendar_auth

        raise SystemExit(run_calendar_auth(open_browser=not args.no_browser))
    if args.cmd == "sms-inject":
        raise SystemExit(_sms_inject(args))
    if args.cmd == "sms-send":
        raise SystemExit(_sms_send_cli(args))
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


def _sms_inject(args: argparse.Namespace) -> int:
    import json
    import urllib.error
    import urllib.request

    from operator_os.sms import WEBHOOK_PATH, INJECT_HEADER, inject_token, webhook_port

    def _strip_label(raw: str, *labels: str) -> str:
        s = (raw or "").strip()
        for label in labels:
            prefix = f"{label}="
            if s.lower().startswith(prefix):
                return s[len(prefix) :].strip()
        return s

    from_num = _strip_label(args.sms_from, "from", "sender")
    text = _strip_label(args.text, "text", "body")
    to_num = _strip_label(args.to, "to") if args.to else ""

    port = int(args.port) if args.port else webhook_port()
    payload = {
        "telnyx_id": f"inject-{int(time.time() * 1000)}",
        "from": from_num,
        "to": to_num,
        "text": text,
    }
    url = f"http://127.0.0.1:{port}{WEBHOOK_PATH}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            INJECT_HEADER: inject_token(),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            print(f"sms-inject ok ({resp.status}) → {url}")
            return 0
    except urllib.error.URLError as e:
        print(f"sms-inject failed: {e} (is `just run` up?)", file=sys.stderr)
        return 1


def _sms_send_cli(args: argparse.Namespace) -> int:
    from operator_os import db as store
    from operator_os.sms import send_sms, sms_configured, sms_from

    if not sms_configured():
        print("SMS not configured (TELNYX_API_KEY + from number)", file=sys.stderr)
        return 1
    try:
        result = send_sms(args.to, args.text)
        store.insert_outbound(
            to_e164=result.to_e164,
            from_e164=result.from_e164 or sms_from(),
            body=result.body,
            telnyx_id=result.telnyx_id or None,
        )
        print(f"sent to {result.to_e164} id={result.telnyx_id or '?'}")
        return 0
    except Exception as e:
        print(f"sms-send failed: {e}", file=sys.stderr)
        return 1


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
         and RealtimeSession.cancel_now() / SipCallSession.hangup() if live
      2) Hook events drained before pulses every loop tick
      3) AudioRouter refuses new playback while on-hook
      4) Service playback is non-blocking so the loop can always see hangup
    Pulse callbacks only enqueue; they never touch audio.
    """
    import queue

    from operator_os.refresh import load_dotenv
    from operator_os.phone import HookFlashClassifier
    from operator_os.sip import (
        SipCallSession,
        SipCredentials,
        SipInboundListener,
        VM_DIR,
        discard_active_recording,
        ensure_voicemail_greeting,
        normalize_nanp,
        sip_configured,
        take_active_recording,
        wav_duration_s,
    )
    from operator_os.sms import (
        WEBHOOK_PATH,
        SmsWebhookServer,
        attach_notify_queue,
        sms_configured,
    )

    load_dotenv()
    ctl = PhoneController()
    decoder = phone.decoder  # type: ignore[attr-defined]
    stop_at = time.monotonic() + max_seconds if max_seconds else None
    q: queue.SimpleQueue[tuple] = queue.SimpleQueue()
    # True while PLAYING_SERVICE audio was started non-blocking; hangup clears it.
    await_service_done = False
    op_session = None  # RealtimeSession | None
    sip_session: SipCallSession | SipInboundListener | None = None
    inbound: SipInboundListener | None = None
    sip_dest: str | None = None
    sip_dtmf: str = ""
    outside_digits = ""
    outside_last_at: float | None = None
    ring_started_at: float | None = None
    sms_announce_id: int | None = None
    sms_ring_deadline: float | None = None
    vm_until: float | None = None
    interdigit_s = float(profile.raw.get("timing", {}).get("outside_number_interdigit_timeout_ms", 2000)) / 1000.0
    inbound_ring_s = float(profile.raw.get("timing", {}).get("inbound_ring_timeout_ms", 45000)) / 1000.0
    sms_ring_s = float(profile.raw.get("timing", {}).get("sms_ring_timeout_ms", 20000)) / 1000.0
    vm_record_s = float(profile.raw.get("timing", {}).get("voicemail_record_ms", 30000)) / 1000.0
    alsa_device = profile.audio.alsa_device
    hook_clf = HookFlashClassifier.from_profile(profile)
    sms_webhook: SmsWebhookServer | None = None
    from operator_os.handset_bridge import HandsetBridge

    handset_bridge = HandsetBridge(handset_alsa=alsa_device)

    def _status(msg: str) -> None:
        print(msg, flush=True)

    def _bridge_start() -> None:
        try:
            handset_bridge.start()
            _status("sip: handset bridge up (line ↔ cradle)")
        except Exception as e:
            _status(f"sip: handset bridge failed {e}")

    def _bridge_stop() -> None:
        if handset_bridge.active:
            handset_bridge.stop()
            _status("sip: handset bridge down")

    def _hangup_sip(*, discard_rec: bool = True) -> None:
        nonlocal sip_session, sip_dest, sip_dtmf, inbound, ring_started_at, vm_until
        _bridge_stop()
        if discard_rec:
            discard_active_recording()
        if sip_session is not None:
            sip_session.hangup()
            sip_session = None
        if inbound is not None:
            inbound.hangup()
            inbound = None
        sip_dest = None
        sip_dtmf = ""
        ring_started_at = None
        vm_until = None

    def _stop_inbound() -> None:
        nonlocal inbound
        if inbound is not None:
            inbound.hangup()
            inbound = None

    inbound_announced = False

    def _ensure_inbound() -> None:
        nonlocal inbound, inbound_announced
        if not live_audio or not sip_configured():
            return
        if inbound is not None and inbound.is_alive():
            return
        # Dead listener: tear down so we don't leak PTY/temp dirs or port 5080.
        if inbound is not None:
            inbound.hangup()
            inbound = None
        creds = SipCredentials.from_env()
        if creds is None:
            return
        try:
            greeting = ensure_voicemail_greeting(audio)
            listener = SipInboundListener(
                credentials=creds,
                alsa_device=alsa_device,
                greeting_wav=greeting,
            )
            listener.start()
            inbound = listener
            # Only announce once (and again after a failed attempt).
            if not inbound_announced:
                _status("sip: inbound registered (waiting for calls)")
                inbound_announced = True
        except Exception as e:
            inbound_announced = False
            _status(f"sip: inbound register failed {e}")
            events.emit("sip", value="inbound_error", detail=str(e)[:120])

    def apply(tr) -> None:
        nonlocal await_service_done, op_session, sip_session, sip_dest, sip_dtmf, inbound
        nonlocal outside_digits, outside_last_at, ring_started_at, vm_until
        for action in tr.actions:
            if action == "sip_hangup":
                # Keep conference WAV across vm_done so we can archive it after flush.
                _hangup_sip(discard_rec=(tr.reason != "vm_done"))
                events.emit("sip", value="hangup")
                continue
            if action == "sip_answer":
                if inbound is not None:
                    try:
                        # Softphone is always on snd-aloop. Live answer joins the
                        # cradle via HandsetBridge; voicemail leaves bridge down.
                        use_handset = tr.reason != "voicemail"
                        if use_handset:
                            _bridge_start()
                        inbound.answer(handset=use_handset)
                        sip_session = inbound
                        inbound = None
                        events.emit(
                            "sip",
                            value="answer",
                            detail="handset" if use_handset else "line",
                        )
                        _status(
                            "sip: answered inbound"
                            + ("" if use_handset else " (virtual line / voicemail)")
                        )
                    except Exception as e:
                        _status(f"sip: answer failed {e}")
                        _hangup_sip()
                continue
            if action == "sip_dial":
                _stop_inbound()
                _start_sip_call()
                continue
            _do_action(action, phone, audio, events, ctl, live_audio)
        if tr.reason:
            _status(f"state={ctl.state.value}  ({tr.reason})")
        if ctl.state == State.ON_HOOK_IDLE:
            await_service_done = False
            outside_digits = ""
            outside_last_at = None
            ring_started_at = None
            vm_until = None
            if op_session is not None:
                op_session.cancel_now()
                op_session = None
            # Drop any live call, then re-register for inbound.
            _bridge_stop()
            if sip_session is not None:
                sip_session.hangup()
                sip_session = None
            sip_dest = None
            sip_dtmf = ""
            _ensure_inbound()
        elif ctl.state in (
            State.DIAL_TONE,
            State.COLLECTING_DIGIT,
            State.PLAYING_SERVICE,
            State.OUTSIDE_LINE,
        ):
            # Off-hook for local use: don't accept new inbound (frees SIP port too).
            _stop_inbound()
        if ctl.state == State.OUTSIDE_LINE and tr.reason == "digit_9":
            outside_digits = ""
            outside_last_at = None

    def _vm_safe_name(e164: str) -> str:
        digits = "".join(c for c in (e164 or "") if c.isdigit())
        return digits or "unknown"

    def _begin_voicemail() -> None:
        """Arm the record window after conference-only answer + greeting.

        Voicemail never touches AudioRouter / handset ALSA — that is owned by
        physical hook + SipInboundListener.answer(handset=...).
        """
        nonlocal vm_until
        if not live_audio or sip_session is None:
            return
        vm_until = time.monotonic() + vm_record_s
        events.emit("voicemail", value="recording")
        _status(f"vm: recording up to {vm_record_s:.0f}s (conference only)")

    def _complete_voicemail(*, save: bool) -> None:
        """Hang up the miss leg; optionally keep the conference WAV + DB row.

        Critical order: archive ``_active.wav`` *before* re-registering inbound.
        ``SipInboundListener.start`` unlinks/recreates that path — taking after
        ``_ensure_inbound`` was why saves came back empty.
        """
        nonlocal sip_session, vm_until
        from operator_os import db as store

        from_e164 = ""
        if isinstance(sip_session, SipInboundListener):
            from_e164 = sip_session.remote_e164()
        vm_until = None

        # Flush the recorder by stopping pjsua, then archive before idle restart.
        if sip_session is not None:
            sip_session.hangup()
            sip_session = None
        time.sleep(0.2)

        if save:
            dest = VM_DIR / f"vm_{int(time.time())}_{_vm_safe_name(from_e164)}.wav"
            path = take_active_recording(dest)
            if path is None:
                _status("vm: no audio captured")
                events.emit("voicemail", value="empty")
            else:
                dur = wav_duration_s(path)
                row = store.insert_voicemail(
                    from_e164=from_e164 or "",
                    path=str(path),
                    duration_s=dur,
                )
                events.emit("voicemail", value="saved", digit=row.id)
                _status(f"vm: saved id={row.id} from={from_e164 or '?'} ({dur:.1f}s)")
        else:
            discard_active_recording()
            events.emit("voicemail", value="discarded")

        # Idle + re-register (new empty ``_active.wav`` is fine; archive is done).
        apply(ctl.handle(Event("vm_done")))

    def _start_sip_call() -> None:
        nonlocal sip_session, sip_dest, sip_dtmf
        dest = sip_dest
        pin = sip_dtmf
        sip_dtmf = ""
        if not dest or not live_audio:
            return
        creds = SipCredentials.from_env()
        if creds is None:
            audio.speak("Outside line is not configured.", wait=False)
            return
        audio.stop()
        try:
            _bridge_start()
            _status(f"sip: dialing {dest}")
            sess = SipCallSession(
                e164=dest,
                credentials=creds,
                alsa_device=alsa_device,
                dtmf_after_confirm=pin,
            )
            sess.start()
            sip_session = sess
            events.emit("sip", value="dial", detail=dest)
            _status(f"sip: call up {dest} (hang up to cancel)")
        except Exception as e:
            _bridge_stop()
            _status(f"sip: dial failed {e}")
            events.emit("sip", value="error", detail=str(e)[:120])
            audio.speak("Unable to complete the call.", wait=False)

    def _start_join_meeting() -> bool:
        """Look up Calendar Meet dial-in; set sip_dest/sip_dtmf. Speak on failure.

        Returns True if place_call should follow.
        """
        nonlocal await_service_done, sip_dest, sip_dtmf
        from operator_os.google_calendar import calendar_configured, pick_meeting_to_join

        if not calendar_configured():
            audio.speak("Calendar is not linked. Run calendar auth on the Pi.", wait=False)
            await_service_done = True
            return False
        if not sip_configured():
            audio.speak("Outside line is not configured.", wait=False)
            await_service_done = True
            return False
        try:
            meet, reason = pick_meeting_to_join()
        except Exception as e:
            _status(f"calendar: {e}")
            events.emit("calendar", value="error", detail=str(e)[:120])
            audio.speak("Unable to read the calendar.", wait=False)
            await_service_done = True
            return False
        if meet is None:
            audio.speak(reason or "No meeting found.", wait=False)
            await_service_done = True
            return False
        title = " ".join((meet.title or "").split()) or "the meeting"
        if len(title) > 48:
            title = title[:45].rstrip() + "…"
        # Must finish before place_call's audio_stop / sip_dial or the phrase is cut.
        audio.speak(f"Connecting to {title}.", wait=True)
        events.emit("calendar", value="join", detail=meet.e164)
        sip_dest = meet.e164
        # Meet expects PIN then #.
        pin = meet.pin.strip()
        if pin and not pin.endswith("#"):
            pin = pin + "#"
        sip_dtmf = pin
        return True

    def on_hook_isr(off_hook: bool) -> None:
        # Raw edges only — flash vs hangup is classified on the main loop so a
        # quick cradle tap does not kill audio / SIP.
        q.put(("hook", off_hook))

    def _stop_sms_ring() -> None:
        nonlocal sms_announce_id, sms_ring_deadline
        if sms_announce_id is not None or sms_ring_deadline is not None:
            try:
                phone.ring_stop()
            except Exception:
                pass
        sms_announce_id = None
        sms_ring_deadline = None

    def _speak_inbound_sms(message_id: int) -> None:
        from operator_os import db as store

        msg = store.get_message(message_id)
        if msg is None:
            return
        who = msg.from_e164 or "unknown"
        from operator_os.sip import speak_phone_number

        spoken_from = speak_phone_number(who) if who != "unknown" else "unknown"
        audio.speak(f"Message from {spoken_from}: {msg.body}", wait=True)
        store.mark_heard(message_id)
        events.emit("sms", value="heard", digit=message_id)

    def _handle_sms_notify(message_id: int) -> None:
        nonlocal sms_announce_id, sms_ring_deadline
        if ctl.state != State.ON_HOOK_IDLE or not live_audio:
            events.emit("sms", value="queued", digit=message_id)
            _status(f"sms: queued id={message_id}")
            return
        if sms_announce_id is not None:
            events.emit("sms", value="queued", digit=message_id)
            _status(f"sms: queued id={message_id} (already ringing)")
            return
        sms_announce_id = message_id
        sms_ring_deadline = time.monotonic() + sms_ring_s
        events.emit("sms", value="ring", digit=message_id)
        _status(f"sms: ringing for id={message_id}")
        try:
            phone.ring_start()
        except Exception as e:
            _status(f"sms: ring failed {e}")
            sms_announce_id = None
            sms_ring_deadline = None

    def _apply_hook_event(kind: str) -> None:
        nonlocal await_service_done, op_session, outside_digits, outside_last_at
        nonlocal ring_started_at, vm_until
        if kind == "off_hook":
            events.emit("hook", value="off_hook")
            _status("hook=OFF_HOOK")
            pending_sms = sms_announce_id
            if pending_sms is not None:
                _stop_sms_ring()
            # Intercept voicemail: keep the live SIP leg, drop the recording,
            # join cradle via bridge (softphone stays on the virtual line).
            if ctl.state == State.VOICEMAIL:
                discard_active_recording()
                vm_until = None
                _bridge_start()
            apply(ctl.handle(Event("off_hook")))
            audio.set_hook(True)
            if pending_sms is not None and live_audio:
                audio.stop()
                _speak_inbound_sms(pending_sms)
            return
        if kind in ("hook_flash", "hook_flash_2"):
            events.emit("hook", value=kind)
            _status(f"hook={kind.upper()}")
            apply(ctl.handle(Event(kind)))
            # Digit-5 mailbox: flash skips to the next message.
            if op_session is not None and hasattr(op_session, "skip_now"):
                try:
                    op_session.skip_now()
                except Exception:
                    pass
            return
        # Definite hangup.
        await_service_done = False
        if op_session is not None:
            op_session.cancel_now()
        _stop_sms_ring()
        _hangup_sip()
        _bridge_stop()
        audio.notify_hangup()
        events.emit("hook", value="on_hook")
        _status("hook=ON_HOOK")
        apply(ctl.handle(Event("on_hook")))
        audio.set_hook(False)
        decoder.reset()
        outside_digits = ""
        outside_last_at = None
        ring_started_at = None
        if op_session is not None:
            op_session.cancel_now()
            op_session = None

    phone.on_hook_change(on_hook_isr)
    phone.on_pulse(lambda: q.put(("pulse", decoder.pending_pulses)))

    if phone.is_off_hook() and ctl.state == State.ON_HOOK_IDLE:
        q.put(("hook", True))

    _status(f"state={ctl.state.value}  (lift handset; Ctrl+C to quit)")
    if live_audio and not sip_configured():
        _status("sip: TELNYX_* not set — digit 9 / inbound disabled")
    elif live_audio:
        _ensure_inbound()
    if live_audio and sms_configured():
        try:
            from operator_os import db as store

            store.init_db()
            sms_webhook = SmsWebhookServer(on_message=attach_notify_queue(q))
            sms_webhook.start()
            _status(f"sms: webhook on 127.0.0.1:{sms_webhook.port}{WEBHOOK_PATH}")
        except Exception as e:
            sms_webhook = None
            _status(f"sms: webhook failed {e}")
    elif live_audio:
        _status("sms: not configured — inbound webhook off")
    try:
        while True:
            if stop_at and time.monotonic() >= stop_at:
                return 0

            hooks, pulses, sms_ids = _drain_prioritized(q)
            for mid in sms_ids:
                _handle_sms_notify(mid)
            for off_hook in hooks:
                for kind in hook_clf.feed(bool(off_hook)):
                    _apply_hook_event(kind)
            for kind in hook_clf.poll(on_hook=not phone.is_off_hook()):
                _apply_hook_event(kind)
            # If we went on-hook, drop pending pulses from this tick.
            if ctl.state == State.ON_HOOK_IDLE:
                pulses = []
                decoder.reset()

            # SMS ring timeout → leave message queued.
            if (
                sms_announce_id is not None
                and sms_ring_deadline is not None
                and time.monotonic() >= sms_ring_deadline
            ):
                mid = sms_announce_id
                _stop_sms_ring()
                events.emit("sms", value="missed", digit=mid)
                _status(f"sms: no answer; queued id={mid}")

            for pending in pulses:
                if ctl.state == State.DIAL_TONE:
                    apply(ctl.handle(Event("pulse")))
                elif ctl.state == State.OUTSIDE_LINE:
                    apply(ctl.handle(Event("pulse")))
                    # Keep interdigit timer from firing mid-digit (rotary is slow).
                    outside_last_at = time.monotonic()
                elif ctl.state != State.COLLECTING_DIGIT:
                    decoder.reset()
                    continue
                _status(f"pulse #{pending}")

            # Inbound INVITE while on-hook → mechanical ring.
            if ctl.state == State.ON_HOOK_IDLE and live_audio:
                if inbound is not None and inbound.is_alive():
                    ev = inbound.poll()
                    if ev == "incoming":
                        ring_started_at = time.monotonic()
                        apply(ctl.handle(Event("ring_start")))
                        time.sleep(0.02)
                        continue
                else:
                    _ensure_inbound()

            if ctl.state == State.INCOMING_RINGING:
                if inbound is not None:
                    ev = inbound.poll()
                    if ev == "ended":
                        _status("sip: caller hung up")
                        discard_active_recording()
                        apply(ctl.handle(Event("incoming_cancel")))
                        time.sleep(0.02)
                        continue
                if (
                    ring_started_at is not None
                    and (time.monotonic() - ring_started_at) >= inbound_ring_s
                ):
                    _status("sip: inbound ring timeout → voicemail")
                    apply(ctl.handle(Event("voicemail_answer")))
                    if ctl.state == State.VOICEMAIL and sip_session is not None:
                        _begin_voicemail()
                    elif ctl.state == State.VOICEMAIL:
                        _status("vm: answer failed; abort")
                        apply(ctl.handle(Event("vm_done")))
                    time.sleep(0.02)
                    continue
                time.sleep(0.02)
                continue

            if ctl.state == State.VOICEMAIL:
                ended = False
                if sip_session is None or not sip_session.is_alive():
                    ended = True
                elif isinstance(sip_session, SipInboundListener):
                    ended = sip_session.poll() == "ended"
                timed_out = vm_until is not None and time.monotonic() >= vm_until
                if ended or timed_out:
                    _complete_voicemail(save=True)
                    time.sleep(0.02)
                    continue
                time.sleep(0.02)
                continue

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

            # SIP call ended remotely → release trunk.
            if ctl.state == State.SIP_CALL and phone.is_off_hook():
                remote_done = False
                if sip_session is None or not sip_session.is_alive():
                    remote_done = True
                elif isinstance(sip_session, SipInboundListener):
                    remote_done = sip_session.poll() == "ended"
                elif isinstance(sip_session, SipCallSession):
                    remote_done = sip_session.remote_ended()
                if remote_done:
                    _flush_queue_pulses(q)
                    apply(ctl.handle(Event("sip_done")))
                    time.sleep(0.02)
                    continue

            # Outside line: wait interdigit silence, then place or cancel.
            if ctl.state == State.OUTSIDE_LINE and phone.is_off_hook():
                pending = decoder.pending_pulses
                if (
                    outside_digits
                    and outside_last_at is not None
                    and pending == 0
                    and (time.monotonic() - outside_last_at) >= interdigit_s
                ):
                    e164 = normalize_nanp(outside_digits)
                    outside_digits = ""
                    outside_last_at = None
                    decoder.reset()
                    _flush_queue_pulses(q)
                    if e164 is None:
                        _status("sip: invalid number")
                        if live_audio:
                            audio.stop()
                            audio.speak(
                                "I'm sorry. That number is not valid.",
                                wait=False,
                            )
                        apply(ctl.handle(Event("outside_cancel")))
                    elif not sip_configured():
                        _status("sip: not configured")
                        if live_audio:
                            audio.stop()
                            audio.speak("Outside line is not configured.", wait=False)
                        apply(ctl.handle(Event("outside_cancel")))
                    else:
                        sip_dest = e164
                        apply(ctl.handle(Event("place_call")))
                        if sip_session is None and live_audio:
                            apply(ctl.handle(Event("sip_done")))
                    time.sleep(0.02)
                    continue

            now_ms = time.monotonic() * 1000
            if ctl.state not in (
                State.DIAL_TONE,
                State.COLLECTING_DIGIT,
                State.OUTSIDE_LINE,
            ):
                time.sleep(0.02)
                continue

            digit = decoder.poll(now_ms)
            if digit is not None:
                pulses_n = 10 if digit == 0 else digit
                events.emit("digit", value=digit, pulses=pulses_n)
                _status(f"digit={digit}")
                if ctl.state == State.OUTSIDE_LINE:
                    outside_digits += str(digit)
                    outside_last_at = time.monotonic()
                    apply(ctl.handle(Event("digit", value=digit)))
                    _status(f"outside digits={outside_digits}")
                    decoder.reset()
                    _flush_queue_pulses(q)
                else:
                    prev = ctl.state
                    tr = ctl.handle(Event("digit", value=digit))
                    events.emit(
                        "state",
                        **{"from": prev.value, "to": tr.state.value, "reason": tr.reason},
                    )
                    apply(tr)
                    decoder.reset()
                    if ctl.state == State.OUTSIDE_LINE:
                        outside_digits = ""
                        outside_last_at = None
                    elif ctl.state == State.PLAYING_SERVICE:
                        result = handle_digit(digit)
                        if result.kind == "join_meeting":
                            if _start_join_meeting():
                                apply(ctl.handle(Event("place_call")))
                                if sip_session is None and live_audio:
                                    apply(ctl.handle(Event("sip_done")))
                            op_session = None
                        else:
                            await_service_done, op_session = _play_service(
                                result, audio, events, live_audio, profile=profile
                            )
                    decoder.reset()
                    _flush_queue_pulses(q)
            time.sleep(0.02)
    except KeyboardInterrupt:
        _status("quit")
        _stop_sms_ring()
        apply(ctl.handle(Event("hangup")))
        _hangup_sip()
        if sms_webhook is not None:
            sms_webhook.stop()
        return 0


def _drain_prioritized(q) -> tuple[list[bool], list[int], list[int]]:
    """Drain the event queue; hooks first, then pulses and sms ids."""
    import queue as _queue

    hooks: list[bool] = []
    pulses: list[int] = []
    sms_ids: list[int] = []
    while True:
        try:
            kind, value = q.get_nowait()
        except _queue.Empty:
            break
        if kind == "hook":
            hooks.append(bool(value))
        elif kind == "pulse":
            pulses.append(int(value))
        elif kind == "sms":
            sms_ids.append(int(value))
    return hooks, pulses, sms_ids


def _flush_queue_pulses(q) -> None:
    """Drop queued pulse notifications; keep hook and sms events."""
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
        events.emit("ring", value="stop")
    elif action == "ring_start":
        if phone.is_off_hook():
            print("ring: skip (off-hook)", flush=True)
        else:
            phone.ring_start()
            print("ring: start", flush=True)
            events.emit("ring", value="start")
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
    if result.kind == "info_desk":
        from operator_os.info_desk import UNAVAILABLE, start_info_desk

        if profile is None:
            audio.speak(UNAVAILABLE, wait=False)
            return True, None
        session = start_info_desk(audio, events, profile=profile)
        if session is None:
            audio.speak(UNAVAILABLE, wait=False)
            return True, None
        return True, session
    if result.kind == "mailbox":
        from operator_os.mailbox import start_mailbox

        session = start_mailbox(audio, events)
        return True, session
    if result.kind == "join_meeting":
        # Live path dials from the main loop; script/sim just announces.
        audio.speak(result.text or "Join meeting.", wait=False)
        return True, None
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
        elif step in ("flash", "hook_flash"):
            tr = ctl.handle(Event("hook_flash"))
            events.emit("hook", value="hook_flash")
            print(f"-> {ctl.state.value} ({tr.reason})")
        elif step in ("flash2", "hook_flash_2"):
            tr = ctl.handle(Event("hook_flash_2"))
            events.emit("hook", value="hook_flash_2")
            print(f"-> {ctl.state.value} ({tr.reason})")
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
    print("Simulator. Commands: off | on | flash | flash2 | digit N | ring | hangup | quit")
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
        if line.lower() == "flash":
            ctl.handle(Event("hook_flash"))
            events.emit("hook", value="hook_flash")
            continue
        if line.lower() in ("flash2", "flash_2", "double"):
            ctl.handle(Event("hook_flash_2"))
            events.emit("hook", value="hook_flash_2")
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
