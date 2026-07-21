# Plant FX samples

Short clips in this directory are cut from Freesound takes for WE302
crossbar seize / release. Full sources live in `data/fx/src/` (local only).

Seize picks randomly among `fx_seize*.wav` on each plant throw.

| File | Role | Source |
|------|------|--------|
| `fx_seize.wav` | 2-click (default bank) | [79369](https://freesound.org/s/79369/) throws ~2.57 s + ~2.63 s |
| `fx_seize_b.wav` | 2-click variant | Same take, ~8.64 s + ~8.70 s |
| `fx_seize_c.wav` | 2-click, tighter | Same take, ~20.77 s + ~20.83 s |
| `fx_seize_3.wav` | 3-click | Same take, composed triple |
| `fx_seize_sharp.wav` | 2-click, brighter | [96673](https://freesound.org/s/96673/) ~0.40 s + ~0.49 s |
| `fx_seize_sharp3.wav` | 3-click, brighter | Same kiss-off take, ~0.17 + ~0.40 + ~0.49 s |
| `fx_release.wav` | Trunk release / kiss-off | [96673](https://freesound.org/s/96673/) slap (~0.42–0.68 s) |

Upstream filenames: `freesound_community-crossbar-timing-relays-79369.mp3`,
`freesound_community-crossbar-kiss-off-96673.mp3`.

Clips are mono 16 kHz S16_LE, peak-normalized, with a short leading pad for
ALSA open latency. Synth fallback remains in `build_crossbar_click()` if the
seize bank is empty.
