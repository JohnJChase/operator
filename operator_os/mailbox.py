"""Digit-5 universal inbox + SMS flash-to-reply.

One phase at a time. Each flash advances the current phase only — no reused
Event across steps (that was the race).
"""

from __future__ import annotations

import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from operator_os.audio import AudioRouter
from operator_os.events import EventLog

RECORD_S = 8.0
# Short offer after each inbox item — no flash → next message (no long stall).
DEFAULT_HINT_WAIT_S = 2.5
# Once the user starts a reply, wait longer for record-end / confirm flashes.
DEFAULT_CONFIRM_WAIT_S = 15.0


def _wait_event(
    ev: threading.Event,
    cancel: threading.Event,
    timeout_s: float,
) -> bool:
    """Wait for ``ev`` or cancel/timeout. Clears ``ev`` only when consumed."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if cancel.is_set():
            return False
        if ev.is_set():
            ev.clear()
            return True
        time.sleep(0.05)
    return False


def _send_reply(
    audio: AudioRouter,
    events: EventLog,
    to_e164: str,
    text: str,
) -> None:
    from operator_os import db as store
    from operator_os.sms import send_sms, sms_configured, sms_from

    if not sms_configured():
        audio.speak("Could not send.", wait=True)
        return
    try:
        result = send_sms(to_e164, text)
        if not result.telnyx_id:
            raise RuntimeError("Telnyx returned no message id")
        store.insert_outbound(
            to_e164=result.to_e164,
            from_e164=result.from_e164 or sms_from(),
            body=result.body,
            telnyx_id=result.telnyx_id,
        )
        events.emit(
            "sms",
            value="reply_sent",
            detail=f"{result.to_e164}:{result.telnyx_id}:{text[:80]}",
        )
        print(
            f"sms: reply sent to={result.to_e164} id={result.telnyx_id} "
            f"text={text[:80]!r}",
            flush=True,
        )
        audio.speak("Sent.", wait=True)
    except Exception as e:
        events.emit("sms", value="reply_error", detail=str(e)[:120])
        print(f"sms: reply failed: {e}", flush=True)
        audio.speak("Could not send.", wait=True)


@dataclass
class SmsReplySession:
    """Live or inbox SMS reply: flash → record → flash → send.

    Phases (flash advances only the current one):
      hint     — short offer; timeout skips quietly (inbox continues)
      record   — flash ends recording early
      confirm  — waiting to send
    """

    audio: AudioRouter
    events: EventLog
    to_e164: str
    hint_wait_s: float = DEFAULT_HINT_WAIT_S
    confirm_wait_s: float = DEFAULT_CONFIRM_WAIT_S
    cancel: threading.Event = field(default_factory=threading.Event, init=False)
    _phase: str = field(default="hint", init=False, repr=False)
    _go: threading.Event = field(default_factory=threading.Event, init=False)
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def wait_done(self, timeout: float | None = None) -> None:
        t = self._thread
        if t is not None:
            t.join(timeout=timeout)

    def cancel_now(self) -> None:
        self.cancel.set()
        self._go.set()
        self.audio.stop()

    def skip_now(self) -> None:
        """Hook flash: advance the current phase."""
        if self._phase == "record":
            self.audio.stop()  # end arecord early
        self._go.set()

    def _run(self) -> None:
        if not self.to_e164.strip():
            self.audio.speak("No number on file.", wait=True)
            return
        if self.cancel.is_set():
            return

        from operator_os.openai_client import api_key_from_env, transcribe_wav

        key = api_key_from_env()
        if not key:
            self.audio.speak("Could not send.", wait=True)
            return

        # --- hint (short; timeout = skip reply, no lecture) ---
        self._phase = "hint"
        self._go.clear()
        self.audio.speak("Flash to reply.", wait=True)
        if not _wait_event(self._go, self.cancel, self.hint_wait_s):
            return

        # --- record ---
        self._phase = "record"
        self._go.clear()
        text = ""
        with tempfile.TemporaryDirectory(prefix="operator-sms-reply-") as tmp:
            wav = Path(tmp) / "reply.wav"
            try:
                self.audio.record(RECORD_S, wav)
            except RuntimeError:
                # Flash (stop) or hangup — keep WAV if any bytes landed.
                pass
            if self.cancel.is_set():
                return
            if not wav.is_file() or wav.stat().st_size < 1000:
                self.audio.speak("I did not catch that.", wait=True)
                return
            self._phase = "stt"
            self.audio.speak("One moment.", wait=True)
            if self.cancel.is_set():
                return
            try:
                text = (transcribe_wav(wav, key) or "").strip()
            except Exception as e:
                self.events.emit("sms", value="reply_stt_error", detail=str(e)[:120])
                self.audio.speak("I did not catch that.", wait=True)
                return

        if self.cancel.is_set():
            return
        if not text:
            self.audio.speak("I did not catch that.", wait=True)
            return

        # --- confirm ---
        self._phase = "confirm"
        self._go.clear()
        self.audio.speak(f"Reply: {text}. Flash to send, or hang up.", wait=True)
        if not _wait_event(self._go, self.cancel, self.confirm_wait_s):
            if not self.cancel.is_set():
                self.audio.speak("Cancelled.", wait=True)
            return
        if self.cancel.is_set():
            return

        self._phase = "send"
        _send_reply(self.audio, self.events, self.to_e164, text)
        self._phase = "done"


@dataclass
class InboxSession:
    audio: AudioRouter
    events: EventLog
    hint_wait_s: float = DEFAULT_HINT_WAIT_S
    confirm_wait_s: float = DEFAULT_CONFIRM_WAIT_S
    request_callback: Callable[[str], None] | None = None
    cancel: threading.Event = field(default_factory=threading.Event, init=False)
    _phase: str = field(default="idle", init=False, repr=False)
    _go: threading.Event = field(default_factory=threading.Event, init=False)
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _reply: SmsReplySession | None = field(default=None, init=False, repr=False)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def is_alive(self) -> bool:
        if self._reply is not None and self._reply.is_alive():
            return True
        return self._thread is not None and self._thread.is_alive()

    def cancel_now(self) -> None:
        self.cancel.set()
        self._go.set()
        if self._reply is not None:
            self._reply.cancel_now()
        self.audio.stop()

    def skip_now(self) -> None:
        if self._reply is not None and self._reply.is_alive():
            self._reply.skip_now()
            return
        if self._phase == "play":
            self.audio.stop()
        self._go.set()

    def _run(self) -> None:
        from operator_os import db as store
        from operator_os.sip import speak_phone_number

        items = store.list_waiting_chrono(limit=20)
        if not items:
            if not self.cancel.is_set():
                self.audio.speak("No waiting messages.", wait=True)
            return
        n = len(items)
        if not self.cancel.is_set():
            spoken = (
                f"You have {n} waiting message."
                if n == 1
                else f"You have {n} waiting messages."
            )
            self.audio.speak(spoken, wait=True)

        for item in items:
            if self.cancel.is_set():
                return
            self._go.clear()
            who = (
                speak_phone_number(item.from_e164)
                if item.from_e164
                else "an unknown caller"
            )
            if item.kind == "sms":
                self._phase = "play"
                self.audio.speak(f"Message from {who}: {item.body}", wait=True)
                store.mark_heard(item.id)
                self.events.emit("sms", value="heard", digit=item.id)
                if self.cancel.is_set():
                    return
                # Flash during the message = skip reply for this item.
                if self._go.is_set():
                    self._go.clear()
                    continue
                self._phase = "reply"
                self._reply = SmsReplySession(
                    audio=self.audio,
                    events=self.events,
                    to_e164=item.from_e164,
                    hint_wait_s=self.hint_wait_s,
                    confirm_wait_s=self.confirm_wait_s,
                )
                self._reply.start()
                self._reply.wait_done()
                self._reply = None
                continue

            # Voicemail
            self._phase = "play"
            self.audio.speak(f"Message from {who}.", wait=True)
            if self.cancel.is_set():
                return
            if self._go.is_set():
                store.mark_voicemail_heard(item.id)
                self._go.clear()
                continue
            path = Path(item.path)
            if path.is_file() and path.stat().st_size > 44:
                self.audio.play_file(path, wait=True)
            store.mark_voicemail_heard(item.id)
            self.events.emit("voicemail", value="heard", digit=item.id)
            if self.cancel.is_set():
                return
            if self._go.is_set():
                self._go.clear()
                continue
            self._phase = "callback"
            self._go.clear()
            self.audio.speak("Flash to call back.", wait=True)
            if not _wait_event(self._go, self.cancel, self.hint_wait_s):
                continue
            if self.cancel.is_set():
                return
            if not item.from_e164.strip():
                self.audio.speak("No number on file.", wait=True)
                continue
            if self.request_callback is not None:
                self.request_callback(item.from_e164)
                self.cancel.set()
                return

        if not self.cancel.is_set():
            self.audio.speak("End of messages.", wait=True)
        self._phase = "idle"


def start_mailbox(
    audio: AudioRouter,
    events: EventLog,
    *,
    hint_wait_s: float = DEFAULT_HINT_WAIT_S,
    confirm_wait_s: float = DEFAULT_CONFIRM_WAIT_S,
    request_callback: Callable[[str], None] | None = None,
) -> InboxSession:
    session = InboxSession(
        audio=audio,
        events=events,
        hint_wait_s=hint_wait_s,
        confirm_wait_s=confirm_wait_s,
        request_callback=request_callback,
    )
    session.start()
    return session


def start_sms_reply(
    audio: AudioRouter,
    events: EventLog,
    to_e164: str,
    *,
    hint_wait_s: float = DEFAULT_HINT_WAIT_S,
    confirm_wait_s: float = DEFAULT_CONFIRM_WAIT_S,
) -> SmsReplySession:
    session = SmsReplySession(
        audio=audio,
        events=events,
        to_e164=to_e164,
        hint_wait_s=hint_wait_s,
        confirm_wait_s=confirm_wait_s,
    )
    session.start()
    return session
