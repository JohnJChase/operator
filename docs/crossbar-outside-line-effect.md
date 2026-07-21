# Crossbar / outside-line seize effect

Reference notes for the electromechanical transition when dialing `9`
(internal dial tone → relay seize → external CO dial tone). Injected into the
WE302 receiver; the capsule provides natural telephone EQ — do not over-filter.

## Sound layers (synthesize, do not need Audacity)

Layer these into one short **two-stage** transient, peaking well above internal
dial tone:

1. **Armature pull-in** — dull low thud (~55–95 Hz) as the magnet starts moving.
2. **Contact slap** (~25–30 ms later) — micro-arc pop + spring snap noise +
   solenoid body (~140 Hz) + brief metallic ring (~1–2.5 kHz).
3. **Contact bounce** — tiny second make a few ms after the slap.

That “clunk…CLACK” is what reads as a mechanical switch on the WE302 receiver.

## Timeline after digit `9`

| Step | Timing | Action |
|---|---|---|
| 1 | Off-hook | Internal office dial tone (350+440). |
| 2 | Dial `9` | Rotary returns 9 pulses; digit commits after interdigit silence. |
| 3 | ~50 ms after last pulse (or immediately on digit commit) | Cut internal tone; fire relay click/thud. |
| 4 | Next 150 ms | Silence (or extremely faint hiss) — the “blind spot”. |
| 5 | After silence | Fade in external CO dial tone cleanly. |

## Playback tips

- Pop should be louder than dial tone (about +3 to +6 dB).
- No heavy telephone EQ on the file — the receiver muffles it naturally.
- Pi owns GPIO + audio; this effect lives in `audio.py` only.

## Status

Implemented as programmatic PCM in `AudioRouter.seize_outside_line()` (no
pre-rendered WAV). Actual SIP/outside number collection is a later phase; this
is the theatrical seize into a second dial tone.
