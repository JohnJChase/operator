# Operator modes on the WE302

Two ways to reach services:

| Digit | Mode |
|-------|------|
| `0` | **Local operator** — spoken dial menu (no cloud) |
| `1` / `2` | News / weather (cached) |
| `3` | **WAMU 88.5** — live NPR stream |
| `4` | **NWS radio** — NOAA Weather Radio KHB36 |
| `5` | **Messages** — universal inbox (SMS + voicemail, chrono); flash skips / reply / callback |
| `7` | **Join meeting** — Google Calendar Meet phone dial-in + PIN |
| `8` | **Information desk** — turn-based STT → tools → Piper |
| `9` | **Outside line** — Telnyx SIP |

## Join meeting (digit 7)

Requires Google OAuth in `.env` (`GOOGLE_OAUTH_CLIENT_ID` / `_SECRET` / `_REFRESH_TOKEN`).

1. One-time on the Pi: `just calendar-auth` (browser consent → refresh token written)
2. Dial **7** off-hook → plant seize → looks up events around now with a phone entry point
3. Speaks “Connecting to \<title\>.” then SIP dials the Meet number; after CONFIRMED, sends PIN#

Ambiguous (multiple dial-ins) or none → spoken refusal; hangup cancels a live call.

## Local menu (digit 0)

Speaks the dial options, then returns to dial tone. Hangup cuts playback.

## Information desk (digit 8)

Period CO feel — **not** a full-duplex chat operator.

1. Dial **8** → plant seize → “Information.”
2. Speak one question (up to ~8 s recording for now)
3. OpenAI transcription → small tool-capable chat model → Piper speaks a chunk
4. Listen again for another turn; hangup cancels

Hook **flash** is a first-class FSM input (optional end-of-utterance later). Definite hangup still wins.

Requires `OPENAI_API_KEY`. Without it, digit 8 speaks an unavailable prompt. Digits `0`/`1`/`2`/`9` still work.

Plant FX (`fx_seize` / `fx_release`) stay on the phone transition chart.

### Function tools (Pi executes)

Weather/news get + play + refresh, device status, local menu, prepare/confirm
outside line, prepare/confirm SMS (`to` + text), list/read unheard messages,
list/play/delete/callback voicemail.

The model never touches GPIO, ALSA, shell, or provider HTTP directly.

See [sms.md](sms.md) for Telnyx Messaging + webhook setup.
See [voicemail.md](voicemail.md) for missed-call recording, MWI stutter, and digit 5 inbox.

### CLI smoke (no mic)

```bash
just operator-test
just operator-test "Refresh the weather and summarize it"
```

## Outside line

See `docs/sip-outside-line.md`.
