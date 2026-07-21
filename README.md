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
just simulate
just simulate --script off,digit:1,hangup
just run
just status
just speak-test
just mic-test
just --list          # all recipes
```

Rev A pins: hook GPIO17, dial GPIO10, ring GPIO23. Audio: ATR2x `plughw:2,0`.

This is a hobby appliance: one process, file caches, JSONL events, CLI
diagnostics. See `docs/pi-dev-environment.md` and
`docs/hardware-profile-verified.md`.

Leave-it-plugged-in: `docs/systemd.md` + `deploy/operator-os.service`.
Operator modes (digit 0 menu, digit 8 Realtime): `docs/ai-operator.md`.
