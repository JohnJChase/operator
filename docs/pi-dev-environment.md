# Pi development environment

Date: 2026-07-20

## Toolchain

- Python 3.13 (system `/usr/bin/python3.13`; pinned in `.python-version`)
- `uv` + project-local `.venv` + `uv.lock`
- `just` task runner
- System packages: `alsa-utils`, `espeak-ng`, `python3-lgpio`

## Setup

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# install just similarly, or use the just.systems installer
just setup
```

`just setup` creates the venv with `--system-site-packages` so gpiozero can
use the OS `python3-lgpio` pin factory on Raspberry Pi OS. A plain
`uv sync` without that flag will fail GPIO open with `BadPinFactory`.

## Commands

```bash
just test           # simulator unit tests
just selftest       # software selftest
just test-hardware  # GPIO + short tone
just simulate       # interactive simulator
just run            # live phone loop
```

Hardware-only:

```bash
uv run operator-os trace-hook
uv run operator-os trace-dial
uv run operator-os ring-test --seconds 2
uv run operator-os audio-test --tone 440
uv run operator-os mic-test --seconds 5
```
