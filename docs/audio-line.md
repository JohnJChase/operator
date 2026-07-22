# Chart + cordboard (plant audio)

Telephone control is a **named state chart**. Handset/line audio is a **cordboard**:
terminals and jumpers. Entering a state installs that state’s **patch**. Features
add chart states and edges — they do not open ALSA, alsaloop, or amixer.

| Layer | Owns |
|-------|------|
| Chart (`state.py`) | States, events, transitions, which patch this state means |
| Plant (`plant.py`) | Jumpers, ATR2x, Loopback bridge, capture hygiene |
| Main / services | Emit events, SIP signaling, fill context (URLs, Meet list) |

See `operator_os.plant.STATE_PATCH` for the state → patch table. Agent rules:
`AGENTS.md` → **Adding a telephone capability**.

## Adding audio behavior

| Want | Do |
|------|-----|
| New listening experience (radio, TTS, …) | State whose patch drives Receiver; context holds URL/text |
| Live talk path | Patch with LineRx→Receiver and Mic→LineTx (`SIP_CALL`) |
| On-hook line feature (voicemail) | Patch with **no** Receiver/Mic jumpers |
| Echo / ATR2x mix | Fix inside plant (gain/AEC), never Meet-specific mute in features |
| Webapp tap / record later | New sink terminal + jumpers (fan-out), same board |

## Terminals

| Terminal | Role |
|----------|------|
| `Receiver` | Earpiece (ATR2x playback) |
| `Mic` | Carbon mic (ATR2x capture) |
| `LineRx` / `LineTx` | Softphone on `snd-aloop` (always) |
| DialTone / Stream / File / Speak | Program sources into Receiver |

Softphone **always** sits on Loopback. Whether the 302 hears or speaks is only
about jumpers to Receiver / Mic.

Examples:

- **DIAL_TONE** — DialTone → Receiver (mic idle)
- **PLAYING_SERVICE** (WAMU) — Stream → Receiver (mic idle)
- **SIP_CALL** — LineRx → Receiver, Mic → LineTx  
  Realization: inbound live = Loopback + alsaloop bridge; **outbound** =
  pjsua on the USB handset directly (`PlantContext.sip_line_mode=handset`).
  ATR2x full-speed cannot sustain alsaloop for Meet (RTP arrives, earpiece
  silent / stutter early media).
- **VOICEMAIL** — greeting/recorder on the **line** only; no Receiver/Mic jumpers

Fan-out (one source → many sinks) is supported by the model for future webapp
taps and call recording; this pass ships today’s sinks.

## Boot (Loopback)

```bash
sudo cp deploy/modules-load.d/operator-aloop.conf /etc/modules-load.d/
sudo cp deploy/modprobe.d/operator-aloop.conf /etc/modprobe.d/
sudo modprobe snd-aloop
aplay -l | grep -i loop
```

## ATR2x note

The USB adapter electrically mixes speaker into mic when **both** legs are open.
The plant lowers capture gain and relies on pjsua AEC when the SIP_CALL patch is
live. Do not add Meet-specific mute timers in feature code.

## Calibration

`audio.sip_mic_capture` in `config/hardware_profile.yaml` (0–30 ALSA steps).
