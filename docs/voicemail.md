# Voicemail (Block 5)

Missed inbound SIP calls leave a message on the Pi.

## Architecture

See [audio-line.md](audio-line.md). Short version:

- Softphone = **snd-aloop** virtual line (never the USB handset).
- Voicemail answers on that line only — no `detach_handset`, no faking hook.
- Live talk starts `HandsetBridge` (alsaloop) so cradle ↔ line join.

## Behavior

1. Inbound INVITE rings the WE302 (`inbound_ring_timeout_ms`, default **25s** ≈ 5 rings).
2. No answer → `answer(handset=False)` on the virtual line → conference greeting
   once → record caller after the beep (`voicemail_record_ms`, default 30s).
3. Caller hangup or record timeout → WAV under `data/voicemail/` + SQLite row
   (archive **before** inbound re-register).
4. Lift during greeting/record **intercepts**: discard recording, start handset
   bridge for a live call.

## Listen

| Path | How |
|------|-----|
| Digit **5** | Mailbox: announce count, play unheard; **hook flash** skips; hangup stops |
| Digit **0** | Menu mentions new voicemail count |
| Info desk (8) | `list_voicemails` / `play_voicemail` / `delete_voicemail` / `callback_voicemail` |

Callback announces the number; place the call with digit **9** (no auto-dial).
