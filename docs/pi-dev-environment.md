# Pi development environment

Date: 2026-07-20

## Toolchain

- Python 3.13 (system `/usr/bin/python3.13`; pinned in `.python-version`)
- `uv` + project-local `.venv` + `uv.lock`
- `just` task runner (preferred CLI front door)
- System packages: `alsa-utils`, `espeak-ng`, `python3-lgpio`
- Python: `piper-tts` in the project venv (see TTS below)

## Setup

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# install just similarly, or use the just.systems installer
just setup
```

`just setup` creates the venv with `--system-site-packages` so gpiozero can
use the OS `python3-lgpio` pin factory on Raspberry Pi OS. A plain
`uv sync` without that flag will fail GPIO open with `BadPinFactory`.

## Hook priority (interrupt)

The switchhook is the highest-priority input — treat it like an interrupt:

1. **GPIO hangup callback** calls `audio.notify_hangup()` / `stop()` immediately
   (cuts the receiver even if the main loop is between ticks).
2. **Main loop** drains hook events before dial pulses every iteration.
3. **Service audio** (news, weather, announcements) starts non-blocking so the
   loop can always observe hangup; never `wait=True` on long playback in `run`.

Pulse callbacks only enqueue counts — they must not call audio.

## TTS (Piper)

Default voice: `hfc_female` (`en_US-hfc_female-medium`).

```bash
just setup-voices
just status          # must show tts=piper
just speak-test
```

espeak-ng is fallback only. Weights live in `voices/hfc_female/*.onnx`
(gitignored).

## Commands

```bash
just --list
just test
just selftest
just test-hardware
just simulate
just run
just status
just refresh            # weather + news (needs NEWSDATA_API_KEY for news)
just refresh --weather
just refresh --news
just trace-hook
just trace-dial
just ring-test          # just ring-test 2
just audio-test         # just audio-test 440 2
just mic-test           # just mic-test 5
just speak-test         # just speak-test "Operator."
just crossbar-test
```
