# WE302 Operator

Local telephone exchange for a Western Electric 302 on a Raspberry Pi.

## Start here

1. Read `AGENTS.md` and adopt it before editing.
2. Read `western-electric-302-ai-telephone-implementation-plan.md`.
3. Read `REV_A_BOARD_AS_BUILT.md` when touching hardware.
4. Read `HAT_PERFBOARD_MIGRATION_PLAN.md` before moving the bench board to the
   permanent HAT/perfboard.
5. Read `PERFBOARD_RINGER_BUILD_GUIDE.md` before soldering the permanent ringer
   section.
6. Read `PERFBOARD_AUDIO_BUILD_GUIDE.md` before soldering receiver, mic, and
   sidetone.
7. Use `config/hardware_profile.yaml` (from the Rev A template).
8. Read `docs/desktop-bridge.md` when wiring a Mac companion client.

## Setup (on the Pi)

```bash
just setup
just test
just selftest
```

## Setup (on the Mac companion)

```bash
just setup-mac
just mac-client
```

## Run

```bash
just simulate
just simulate --script off,digit:1,hangup
just run
just status
just mac-client       # on the Mac, after setting OPERATOR_PI_URL + token
just speak-test
just mic-test
just --list          # all recipes
```

Rev A pins: hook GPIO17, dial GPIO22, ring GPIO6 (HAT). Audio: ATR2x `plughw:2,0`.

This is a hobby appliance: one process, file caches, JSONL events, CLI
diagnostics. See `docs/pi-dev-environment.md` and
`docs/hardware-profile-verified.md`.

Leave-it-plugged-in: `docs/systemd.md` + `deploy/operator-os.service`.
Operator modes (digit 0 menu, digit 8 Realtime, digit 9 SIP): `docs/ai-operator.md`,
`docs/sip-outside-line.md`.
Mac companion bridge: `docs/desktop-bridge.md`.
Central-office / multi-extension roadmap: `docs/operator-exchange-plan.md`.
