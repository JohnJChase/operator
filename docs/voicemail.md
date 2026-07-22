# Voicemail (Block 5)

Missed inbound SIP calls leave a message on the Pi.

## Behavior

1. Inbound INVITE rings the WE302 (`inbound_ring_timeout_ms`, default 45s).
2. No answer → answer the SIP leg on-hook → spoken greeting + tone → record
   (`voicemail_record_ms`, default 30s) via pjsua conference `--auto-rec`.
3. Caller hangup or record timeout → WAV under `data/voicemail/` + row in
   `operator.sqlite3` (`voicemails` table).
4. Lift during greeting/record **intercepts** into a live call (recording discarded).

**Ceiling:** greeting uses Piper on the shared ALSA device while pjsua holds the
call. If they fight, the caller may hear silence before the record window; the
WAV path still works.

Answered human calls also get a temporary `_active.wav`; it is discarded on
hangup (not saved as voicemail).

## Listen

| Path | How |
|------|-----|
| Digit **5** | Mailbox: announce count, play unheard; **hook flash** skips; hangup stops |
| Digit **0** | Menu mentions new voicemail count |
| Info desk (8) | `list_voicemails` / `play_voicemail` / `delete_voicemail` / `callback_voicemail` |

Callback announces the number; place the call with digit **9** (no auto-dial).
