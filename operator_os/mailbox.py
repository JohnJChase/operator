"""Digit-5 voicemail mailbox: play unheard messages; flash skips."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

from operator_os.audio import AudioRouter
from operator_os.events import EventLog


@dataclass
class MailboxSession:
    audio: AudioRouter
    events: EventLog
    cancel: threading.Event = field(default_factory=threading.Event, init=False)
    skip: threading.Event = field(default_factory=threading.Event, init=False)
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def cancel_now(self) -> None:
        self.cancel.set()
        self.skip.set()
        self.audio.stop()

    def skip_now(self) -> None:
        """Hook flash: stop current clip and advance."""
        self.skip.set()
        self.audio.stop()

    def _run(self) -> None:
        from operator_os import db as store
        from operator_os.sip import speak_phone_number

        rows = store.list_unheard_voicemails(limit=20)
        if not rows:
            if not self.cancel.is_set():
                self.audio.speak("No new voicemail.", wait=True)
            return
        n = len(rows)
        if not self.cancel.is_set():
            spoken = (
                f"You have {n} new voicemail."
                if n == 1
                else f"You have {n} new voicemails."
            )
            self.audio.speak(spoken, wait=True)
        for vm in rows:
            if self.cancel.is_set() or self.audio.is_on_hook:
                return
            self.skip.clear()
            who = (
                speak_phone_number(vm.from_e164)
                if vm.from_e164
                else "an unknown caller"
            )
            self.audio.speak(f"Message from {who}.", wait=True)
            if self.cancel.is_set() or self.audio.is_on_hook:
                return
            if self.skip.is_set():
                store.mark_voicemail_heard(vm.id)
                continue
            path = Path(vm.path)
            if path.is_file() and path.stat().st_size > 44:
                self.audio.play_file(path, wait=True)
            store.mark_voicemail_heard(vm.id)
            self.events.emit("voicemail", value="heard", digit=vm.id)
            if self.cancel.is_set() or self.audio.is_on_hook:
                return
        if not self.cancel.is_set() and not self.audio.is_on_hook:
            self.audio.speak("End of voicemail.", wait=True)


def start_mailbox(audio: AudioRouter, events: EventLog) -> MailboxSession:
    session = MailboxSession(audio=audio, events=events)
    session.start()
    return session
