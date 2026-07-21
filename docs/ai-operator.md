# Phase 7 — AI operator

Digit `0` starts a turn-based AI operator session when `OPENAI_API_KEY` is set
in `.env`. Without a key (or on API failure), the handset hears the unavailable
prompt; digits 1/2/9 still work.

## Interaction model

1. Speak greeting (`Operator. Go ahead, please.`).
2. Record ~5s from the handset mic (`arecord`, interruptible on hangup).
3. Transcribe via `POST /v1/audio/transcriptions` (`gpt-4o-mini-transcribe`).
4. Tool loop via Responses API (`POST /v1/responses`, `gpt-4o-mini`).
5. Speak reply with Piper; repeat until hangup, goodbye, or turn limit.

Hangup calls `OperatorSession.cancel_now()` and `audio.notify_hangup()` so
playback/recording stop immediately.

## Tool boundary

The model only sees local functions in `operator_os/ai_operator.py`
(`get_weather`, `get_news`, `get_device_status`, `play_*`, prepare/confirm for
outside line and messages). No GPIO, shell, or provider HTTP from the model.
Outside line and messages require prepare → user confirm → confirm_*; SMS send
still refuses until a provider exists.

## CLI smoke (no mic)

```bash
just operator-test
just operator-test "Play the news"
```

Uses the same tools + Responses path; speaks the reply on the handset.
