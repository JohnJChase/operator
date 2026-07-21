# WE302 Operator

Local telephone exchange for a Western Electric 302 on a Raspberry Pi.

## Start here

1. Read `AGENTS.md` and adopt it before editing.
2. Read `western-electric-302-ai-telephone-implementation-plan.md`.
3. Read `REV_A_BOARD_AS_BUILT.md` when touching hardware.
4. Use `config/hardware_profile.yaml` (from the Rev A template).

## Setup (on the Pi)

```bash
just setup
just test
just selftest
```

## Run

```bash
just simulate                          # no GPIO
uv run operator-os simulate --script off,digit:1,hangup
just run                               # live hook/dial/ring/audio
```

Rev A pins: hook GPIO17, dial GPIO10, ring GPIO23. Audio: ATR2x `plughw:2,0`.

This is a hobby appliance: one process, file caches, JSONL events, CLI
diagnostics. See `docs/pi-dev-environment.md` and
`docs/hardware-profile-verified.md`.
