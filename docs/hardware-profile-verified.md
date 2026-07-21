# Hardware profile verification (Rev A)

Date: 2026-07-20

Profile: `config/hardware_profile.yaml` (`rev_a_direct_bypass_gpio10`)

## Pins

| Function | BCM | Result |
|---|---|---|
| Hook | 17 | Manual: many clean ON/OFF transitions via `trace-hook` |
| Dial pulse | 10 | Manual: digits `1-9` and `0` (10 pulses) via `trace-dial` |
| Ring | 23 | `ring-test --seconds 1.5` energized and exited clean |

## Audio

| Check | Result |
|---|---|
| ALSA device | `plughw:2,0` (ATR2x-USB card 2) present |
| 440 Hz tone | Played via `audio-test` / hardware selftest |
| Mic capture | `mic-test --seconds 2` wrote `data/recordings/mic-test.wav` (~64 KB) |

## Software ring cutoff

Ring loop polls hook while energized and refuses to start off-hook. Full
"stops within 100 ms of off-hook" timing still needs a manual lift-during-ring
confirmation.

## Manual remaining

- Confirm ring stops within ~100 ms of off-hook (`ring-test`, lift mid-ring).
- Live loop: `just run` → lift → dial tone → dial a digit → hangup.

## Environment note

GPIO requires the project venv to see system `python3-lgpio`
(`uv venv --system-site-packages`; see `docs/pi-dev-environment.md`).
