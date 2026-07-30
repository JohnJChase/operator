# WE302 Rev A HAT Perfboard Migration Plan

Date: 2026-07-27

This plan moves the current known-good breadboard onto a Pi HAT perfboard one
section at a time. The electrical source of truth remains
`REV_A_BOARD_AS_BUILT.md` and `rev_a_board_netlist.yaml`; this document is the
physical build order, layout strategy, and acceptance checklist.

## Photo Review

Photos reviewed:

- `/Users/jchase/Downloads/IMG_9801.HEIC`
- `/Users/jchase/Downloads/IMG_9802.HEIC`
- `/Users/jchase/Downloads/IMG_9803.HEIC`
- `/Users/jchase/Downloads/IMG_9804.HEIC`

The visible breadboard matches the saved Rev A design at the topology level:

- Black Magic ring generator is its own physical island.
- Axicom relay, NPN driver, base resistor, and flyback diode are colocated near
  the Black Magic low-voltage input.
- Two trim pots and electrolytic capacitors are visible in the audio section,
  matching the mic-drive and sidetone controls.
- The Arduino-style board is not part of the final system; the permanent board
  still needs the phone screw terminals moved onto the same HAT/perfboard.
- The build is already using the correct architectural split: ring, GPIO
  controls, and audio are separate domains sharing only the low-voltage board
  power/common where the Rev A docs say they should.

What the photos cannot prove:

- Exact resistor values.
- Electrolytic polarity.
- Transistor pinout.
- Relay coil/contact pin selection.
- Continuity of each thermostat-wire conductor back to the phone.

Those must be verified with the meter during migration.

## Layout Principle

Do not copy the breadboard geometry. Copy the nets.

The HAT should be laid out by electrical domain:

1. Ring high voltage at one outside edge.
2. Relay and Black Magic low-voltage switching adjacent to the ring generator.
3. GPIO hook/pulse inputs near the Pi header and phone terminal block.
4. Audio at the opposite quiet edge, with pots reachable after assembly.
5. One clear +5V bus and one clear GND bus from the Pi header.

## Proposed Component-Side Layout

This is a relative layout, not a hole-by-hole drawing. Rotate it to match the
actual HAT header orientation before soldering.

```text
+--------------------------------------------------------------------------------+
| PHONE CABLE EDGE / STRAIN RELIEF                                              |
|                                                                                |
|  [HV terminals]       [control terminals]          [audio terminals]           |
|   L1   K              Y   BK   BB                  WHITE   RED/R   BLACK       |
|    |   |              |    |    |                    |       |       |          |
|    v   v              v    v    v                    v       v       v          |
| +------------+     +----------------+          +---------------------------+    |
| | RING HV    |     | GPIO INPUTS    |          | AUDIO / SIDETONE          |    |
| | Black Magic|     | R_HOOK 1k      |          | R_HP 220R                 |    |
| | HV only to |     | R_PULSE 1k     |          | mic 220R + 10k pot        |    |
| | L1 and K   |     | Y to GND       |          | C_MIC_REC                |    |
| +------------+     +----------------+          | C_SIDE 470uF + 1k pot     |    |
|        |                  |                    | R_SIDE_MIN 10R            |    |
|        v                  v                    +---------------------------+    |
| +-------------------------------+                                                |
| | RELAY / RING LOW-VOLTAGE      |                                                |
| | K1 relay, Q1, D1, R_Q1_BASE   |                                                |
| | relay switches Black Magic +5V|                                                |
| +-------------------------------+                                                |
|                                                                                |
| +5V BUS: Pi 5V -> relay coil, relay COM, mic drive, decoupling                 |
| GND BUS: Pi GND -> Y, RED/R, Q1 emitter, ATR2x sleeves, BM low-voltage GND     |
|                                                                                |
| PI HEADER EDGE: GPIO17 hook, GPIO22 pulse, GPIO6 ring, 5V, GND                |
+--------------------------------------------------------------------------------+
```

## Mechanical Rules

- Prefer isolated-pad perfboard for the permanent build. Breadboard-pattern
  proto boards can work only if every connected strip is deliberately mapped
  before soldering; otherwise relay pins and buses can be silently shorted.
- Put the phone screw terminals on the HAT/perfboard, not on a separate landing
  board.
- Strain-relieve the 8-conductor thermostat cable before the terminal block.
- Keep `L1` and `K` at the board edge with a visible no-solder gap around them.
- Do not route `L1` or `K` under GPIO, audio, the Pi header, or the pots.
- Keep the Black Magic high-voltage output isolated from board GND, +5V, GPIO,
  and audio.
- Put both trim pots where they can be adjusted with the board mounted.
- If the board becomes too tight, move the Black Magic module off-HAT before
  compromising high-voltage spacing. Keep the relay, GPIO inputs, terminals, and
  audio interface on the HAT.

## Migration Phases

### Phase 0: Label And Meter The Breadboard

Before removing anything:

- Label every phone conductor: `Y`, `BK`, `BB`, `L1`, `K`, `WHITE_RX`,
  `RED_R_COMMON`, `BLACK_MIC`.
- Label the Pi/ATR2x conductors: `5V`, `GND`, `GPIO17`, `GPIO22`, `GPIO6`,
  headphone tip, headphone sleeve, mic tip, mic sleeve.
- Photograph the working breadboard from straight above and from both sides.
- Meter continuity from each phone terminal to its matching breadboard node.

Acceptance:

- Hook still reads on-hook HIGH and off-hook LOW.
- Dial pulses still count.
- Ring still works.
- Receiver, mic recording, and sidetone still work.

### Phase 1: Build HAT Power And Ground Backbone

Solder only:

- Pi 5V header connection to the board +5V bus.
- Pi GND header connection to the board GND bus.
- Optional 0.1uF capacitor across +5V/GND near the relay area.
- Optional 470uF bulk capacitor across +5V/GND near the Black Magic
  low-voltage input.

Power-off checks:

- +5V to GND is not shorted.
- GND bus has continuity to Pi GND.
- +5V bus has continuity to Pi 5V.

Power-on checks:

- Board reads about 5V between +5V and GND.
- Pi does not reboot or brown out.

### Phase 2: Install Phone Terminal Block

Install one 8-position block or grouped terminal blocks:

```text
L1 | K | Y | BK | BB | WHITE_RX | RED_R_COMMON | BLACK_MIC
```

Recommended grouping if using multiple blocks:

```text
L1 K        Y BK BB        WHITE_RX RED_R_COMMON BLACK_MIC
ring HV    controls       audio
```

Power-off checks:

- `Y` has continuity to board GND.
- `RED_R_COMMON` has continuity to board GND.
- `L1` and `K` do not have continuity to board GND, +5V, GPIO, or audio.
- `BK` and `BB` do not have continuity to +5V.

### Phase 3: Move Ring Section

Solder:

- GPIO6 physical pin 31 -> `R_Q1_BASE` 1k -> Q1 base.
- Q1 base -> 5.1k pulldown -> board GND.
- Q1 emitter -> board GND.
- Q1 collector -> relay coil low side.
- Relay coil high side -> +5V.
- Flyback diode across relay coil, stripe/cathode to +5V, anode to Q1
  collector.
- Relay COM -> +5V.
- Relay NO -> Black Magic low-voltage +5V input.
- Black Magic low-voltage GND input -> board GND.
- Black Magic HV output 1 -> `L1`.
- Black Magic HV output 2 -> `K`.

Acceptance:

- GPIO6 LOW: relay off, Black Magic unpowered, no ring.
- GPIO6 HIGH: relay energizes and bell rings.
- With Pi powered and no ring software running, relay remains off.
- If the breadboard hook line is still connected, hook going off-hook causes
  software to turn GPIO6 LOW immediately. Otherwise retest this after Phase 4.
- Neither `L1` nor `K` has continuity to board GND after the section is built.

### Phase 4: Move Hook And Pulse Inputs

Solder:

```text
GPIO17 physical pin 11 -> 1k -> BK
GPIO22 physical pin 15 -> 1k -> BB
Y -> board GND
```

Acceptance:

- Hook on-hook reads HIGH.
- Hook off-hook reads LOW.
- Dial pulse rests HIGH.
- Dial return pulses count LOW closures to `Y/GND`.
- Ring still works after this section is added.

### Phase 5: Move Receiver Audio

Solder:

```text
ATR2x headphone tip -> 220R -> WHITE_RX
ATR2x headphone sleeve -> RED_R_COMMON / board GND
```

Acceptance:

- A test tone plays through the receiver.
- Ring, hook, and pulse still work.
- Receiver audio is not routed through `L1` or `K`.

### Phase 6: Move Carbon Mic Recording

Solder:

```text
+5V -> 220R -> 10k mic-drive pot as rheostat -> BLACK_MIC
RED_R_COMMON -> board GND
BLACK_MIC -> C_MIC_REC -> ATR2x mic tip
ATR2x mic sleeve -> board GND
```

Pot wiring:

```text
220R output -> pot outer lug A
pot middle/wiper -> BLACK_MIC
pot outer lug B -> jumper to middle/wiper
```

If `C_MIC_REC` is electrolytic:

```text
C_MIC_REC + / long leg -> BLACK_MIC
C_MIC_REC - / short leg -> ATR2x mic tip
```

Acceptance:

- Recording works.
- Mic drive pot changes recording level.
- Normal speech does not clip.
- Ring, hook, pulse, and receiver audio still work.

### Phase 7: Move Passive Sidetone

Solder:

```text
BLACK_MIC -> 470uF sidetone cap -> 1k sidetone pot -> 10R -> WHITE_RX
```

Pot wiring:

```text
470uF cap - / short leg -> pot outer lug A
pot middle/wiper -> 10R -> WHITE_RX
pot outer lug B -> jumper to middle/wiper
```

Cap polarity:

```text
470uF + / long leg -> BLACK_MIC
470uF - / short leg -> sidetone pot
```

Acceptance:

- Sidetone is audible and adjustable.
- Receiver playback remains usable.
- Mic recording remains usable.
- Ring, hook, and pulse still work.

### Phase 8: Final Dress And Full Regression

Before mounting:

- Trim excess leads.
- Inspect for solder bridges.
- Add labels for terminals, pots, `L1/K`, +5V, and GND.
- Add strain relief to the thermostat cable.
- Secure or insulate the Black Magic high-voltage side.

Full acceptance:

- Pick up: hook changes HIGH -> LOW.
- Hang up: hook changes LOW -> HIGH.
- Dial digits 0 through 9: pulse counts decode correctly.
- Ring command rings only while commanded.
- Off-hook during ring stops ring in software.
- Receiver playback works.
- Mic recording works.
- Sidetone is adjustable.
- Pi remains stable during ring and audio activity.

## Do Not Carry Over From Breadboard

- Do not keep loose Dupont jumpers for final current-carrying or high-voltage
  paths.
- Do not use the Arduino-style board as a separate permanent terminal landing
  pad.
- Do not rely on unlabeled breadboard rails.
- Do not leave the Black Magic high-voltage output exposed where it can be
  touched or shorted.

## Recommended Build Order Summary

```text
1. Dry-fit board and terminal blocks.
2. Solder +5V/GND backbone.
3. Solder phone terminal block.
4. Move ring island and test.
5. Move hook/pulse inputs and test.
6. Move receiver audio and test.
7. Move mic recording and test.
8. Move sidetone and test.
9. Dress, label, strain-relieve, and run full regression.
```
