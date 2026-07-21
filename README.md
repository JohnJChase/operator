# WE302 Operator

Blank-slate build handoff for the Western Electric 302 AI Telephone Exchange.

Start here:

1. Read `AGENTS.md` and explicitly adopt it before editing.
2. Read `western-electric-302-ai-telephone-implementation-plan.md`.
3. Read `REV_A_BOARD_AS_BUILT.md` when touching hardware.
4. Use `we302_hardware_profile.template.yaml` as the starting hardware profile.

Repository remote:

```text
git@github.com:JohnJChase/operator.git
```

The current Rev A hardware profile is:

```text
hook: GPIO17
dial pulse: GPIO10
ring relay: GPIO23
audio: direct handset bypass through ATR2x
```

This is a hobby appliance, not a commercial SaaS app. Keep the implementation
small: one process, a few files, CLI diagnostics, file caches, JSONL events, and
the smallest tests that catch real regressions.

