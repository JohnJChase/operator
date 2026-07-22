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
| Digit **5** | Universal inbox: unheard SMS + voicemail, oldest first. Flash **during** a clip skips; after a VM, “Flash to call back” dials `from_e164` over SIP. Hangup stops. |
| Off-hook | Stutter dial tone when any SMS or VM waits (`mwi_stutter_ms`), then normal dial. |
| Digit **0** | “N waiting messages” + dial 5 |
| Info desk (8) | `list_voicemails` / `play_voicemail` / `delete_voicemail` / `callback_voicemail` |

Desk `callback_voicemail` still announces the number for a manual digit-**9** place; inbox flash-to-callback auto-dials.
