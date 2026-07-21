# Operator modes

Two ways to reach services on the WE302:

| Digit | Mode |
|-------|------|
| `0` | **Local operator** — spoken dial menu (no cloud) |
| `1` / `2` | News / weather (cached) |
| `3` | **WAMU 88.5** — live NPR stream ([playlist](https://static.wamu.org/streams/live/1/mp3.1.pls)) |
| `8` | **Realtime information operator** — live voice via GPT Realtime |
| `9` | **Outside line** — Telnyx SIP (`docs/sip-outside-line.md`) |

## Local menu (digit 0)

Speaks:

> Operator. Dial 1 for news, 2 for weather, 3 for WAMU,
> 8 for the information operator, 9 for outside line. Dial 0 to hear this again.

Then returns to dial tone so you can dial the next digit. Hangup cuts a live
stream the same as any other service.

## Realtime operator (digit 8)

Server-to-server WebSocket to `gpt-realtime-2.1`
([Realtime WebSocket](https://developers.openai.com/api/docs/guides/realtime-websocket),
[Realtime with tools](https://developers.openai.com/api/docs/guides/realtime-with-tools)).

Supervisory logic is a small CO-style mode chart (`LISTEN` → `THINKING` → `SPEAK` →
`ECHO` → `LISTEN`): one path, event → action or nothing. Hangup releases from every
mode. WS/capture only drop signals into a bay thread; they do not flip policy flags.

**Plant FX live on the transition chart** (`fx_seize` / `fx_release` / `fx_outside` via
`AudioRouter.play_plant`) — not sprinkled in service helpers. Same for the phone digit
FSM: menu seize/release and outside line are actions on the edge.

- **LISTEN** — energy gate may uplink to OpenAI (brain / VAD / transcript).
- **THINKING** — mic held; waiting on transcript or tools.
- **SPEAK** — mic held; transition plays `fx_seize`, then Piper. Time/weather/news/status
  are far trunks; `Operator.` is the operator jack.
- **ECHO** — mic held for `echo_guard_ms` so receiver bleed does not retrigger VAD.
  Return to **LISTEN** is `fx_release` then open mic. Digit menu (0/1/2/…) uses the
  phone chart’s `fx_seize` / `fx_release` around content and dial tone.
- Hangup is a **hardware cutoff**: `notify_hangup()` marks on-hook and kills audio.
- Cloud audio unused (`output_modalities: text`). Autotune sets gate *above* the noise floor.

## Guided autotune (preferred)

```bash
just realtime-autotune
```

Handset off-hook. Follow the on-screen script (silence → say “What time is it?” →
silence). The tool measures mic levels and whether the model responds, then sets
gate / mic gain / ear gain / echo guard / VAD and writes `config/hardware_profile.yaml`.

## Live tuning bench (manual)

```bash
just realtime-tune
```

Handset off-hook. Top of the screen:

- **Door banner** — green `DOOR OPEN` (audio to OpenAI), red `DOOR CLOSED`,
  yellow `OPERATOR TALKING` (**Space** interrupts), or yellow `ECHO GUARD`
  (waiting out receiver bleed).
- **dB gauge** (−60…0): markers `G` gate, `*` mic after **mic gain**, `B` barge.
  Line also shows raw vs after-gain levels.
- **mic gain ×** boosts your voice (and bleed). **ear gain ×** turns the receiver
  down so bleed does not drown you — raise mic, lower ear until speech clears the floor.

| Keys | Action |
|------|--------|
| **Space** | **Interrupt** operator immediately |
| `←` `→` (or `h` `l`) | Select knob |
| `↑` `↓` (or `j` `k`) | Change selected knob |
| `n` | Cycle noise reduction |
| `g` | Replay greeting |
| `c` | Clear input buffer |
| `w` | Write knobs to `config/hardware_profile.yaml` |
| `q` / Esc | Quit |

### What each knob does

Think of the path as: **mic → gate → (hangover) → OpenAI VAD → model answers**.

| Knob | What it controls | If too low / open | If too high / closed |
|------|------------------|-------------------|----------------------|
| **gate dBFS** | Local “is someone talking?” energy door before audio is sent | Idle hiss shows `UP`; static gets answered | Your voice stays `idle`; operator never hears you |
| **hangover ms** | How long the door stays open after speech drops | Cuts off ends of words | Holds `UP` on trailing noise |
| **echo guard ms** | Mute after operator finishes (bleed settle) | Answers its own voice / loops | Feels slow to hear you after it talks |
| **barge dBFS** | How loud you must talk **while** the operator is speaking to interrupt (`0` = off) | Accidental interrupts from bleed/noise | Can’t interrupt long replies |
| **VAD threshold** | OpenAI’s sensitivity to treat uplink as speech (0–1, higher = pickier) | Answers to noise/static | Misses quiet speech even when `UP` |
| **silence ms** | Quiet time before OpenAI decides you finished talking | Cuts you off mid-thought | Slow to answer after you stop |
| **prefix ms** | Audio kept just before VAD detected speech | (rarely matters) | (rarely matters) |
| **noise** (`n`) | OpenAI near/far-field noise filter | — | Cycle `near_field` / `far_field` / `off` |

**Meter states**

- `idle` — gate closed; nothing sent upstream (good when you’re silent).
- `UP` — gate open; mic audio is going to OpenAI.
- `TALK` — operator / trunk is speaking; uplink muted (no barge-in).
- `ECHO` — post-speech settle; uplink forced closed even if the meter spikes from bleed.

**How they interact**

1. Gate decides whether the Pi sends audio at all.
2. VAD only sees what passed the gate — so a closed gate means VAD never fires.
3. If gate is open on hiss, VAD may still reject it if threshold is high — or answer if threshold is low.
4. During `TALK` / `ECHO` the supervisory mode holds the mic; interrupt is hush/hangup only.

### Tuning procedure (do this in order)

Sit with the handset to your ear, `just realtime-tune` running, eyes on the meter.

**1. Find the noise floor (don’t talk)**  
After the greeting finishes, stay silent 5–10s.

- Note typical `rms=` while quiet (often around −50 to −35 on this carbon mic).
- Goal: meter shows **`idle`**, not `UP`.
- If it flickers `UP` on silence alone → select **gate**, press **↑** (raise toward −40, −38, …) until silent stays `idle`.

**2. Open the gate for your voice**  
Say a clear phrase: “What time is it?”

- Goal: meter goes **`UP`** while you talk, back to **`idle`** shortly after.
- If it stays `idle` while you talk → **↓** gate (more sensitive, e.g. −48, −50).
- If it stays `UP` long after you stop → lower **hangover** (↓), or raise gate a bit.

**3. Make answers track real speech (VAD)**  
With gate behaving, ask something short again.

- If it answers when you were silent → raise **VAD threshold** (↑ toward 0.8–0.9).
- If `UP` while you talk but it still doesn’t understand / doesn’t reply → lower VAD threshold slightly, or check you’re not mumbling past the gate.
- If it cuts you off mid-sentence → raise **silence ms** (↑).
- If it waits forever after you finish → lower **silence ms** (↓).

**4. Barge-in (optional)**  
Press `g` for a long greeting, then try to interrupt by talking over it.

- If you can’t break in → lower **barge** (more negative is *not* easier — wait: barge is “must be louder than X”. More negative like −35 is easier to hit than −20. So **↓** barge (e.g. −32) = easier interrupt. **↑** toward 0 = harder / off at 0.
- If it interrupts itself from earpiece bleed → raise barge toward −20, or set **0** to disable.

**5. Save**  
When silent=`idle`, speech=`UP`, answers feel sane: press **`w`**. That writes into `config/hardware_profile.yaml` for digit `8`.

### Good “done” checklist

- [ ] Silence → `idle` for several seconds  
- [ ] Talking → `UP`, then `idle` after you stop  
- [ ] Short question gets a short answer (not a capability speech)  
- [ ] Idle static does **not** get a reply  
- [ ] (Optional) You can barge in on a long reply  
- [ ] Pressed **`w`**

### Function tools (Pi executes)

Weather/news get + play + refresh, device status, local menu, prepare/confirm
outside line, prepare/confirm message (send still refused until SMS exists).

The model never touches GPIO, ALSA, shell, or provider HTTP directly.

MCP connectors (Calendar / Slack / Gmail) and SIP Meet dial-in are later slices.

### Unavailable

Without `OPENAI_API_KEY` (or on WS failure), digit `8` speaks the unavailable
prompt. Digits `0`/`1`/`2`/`9` still work.

## CLI smoke (no mic)

```bash
just operator-test
just operator-test "Refresh the weather and summarize it"
```

Uses Realtime with text modalities + the same local tools.
