# Crossbar / outside-line seize effect

Reference notes for the electromechanical transition when dialing `9`
(internal dial tone → relay seize → external CO dial tone). Injected into the
WE302 receiver; the capsule provides natural telephone EQ — do not over-filter.

## Sound layers (synthesize, do not need Audacity)

Layer these into one short transient, peaking ~3–6 dB above internal dial tone:

1. **DC spark (pop)** — 1–2 ms single-cycle square / spike (micro-arc tick).
2. **Spring snap (click)** — 15–20 ms white noise, instant attack, fast
   exponential decay.
3. **Solenoid slap (thud)** — 120–150 Hz sine, 40–60 ms decay (closet magnet).

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
