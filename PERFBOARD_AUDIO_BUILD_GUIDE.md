# WE302 Rev A Perfboard Audio Build Guide

Date: 2026-07-28

This guide builds the permanent-board audio section after ring, hook, and pulse
already pass. Build in this order:

1. Receiver playback.
2. Carbon mic drive and recording.
3. Passive hardware sidetone.

Do not build sidetone first. Receiver and mic should each work alone before
they are tied together by the sidetone branch.

## Audio Nets

Phone-side terminals:

```text
WHITE_RX     = receiver signal
RED_R_COMMON = receiver/mic common, tied to board GND
BLACK_MIC    = carbon mic signal/bias node
```

ATR2x/sound-card terminals:

```text
HP_TIP     = headphone output signal
HP_SLEEVE  = headphone output common
MIC_TIP    = mic input signal
MIC_SLEEVE = mic input common
```

Board buses:

```text
+5V bus = Pi 5V
GND bus = Pi GND
```

## Components

```text
R_HP_SERIES = 220 ohm
R_MIC_MIN   = 220 ohm
RV_MIC      = 10k pot, wired as rheostat
C_MIC_REC   = existing mic recording/gain coupling cap
C_SIDE      = 470uF electrolytic sidetone cap
RV_SIDE     = 1k pot, wired as rheostat
R_SIDE_MIN  = 10 ohm
```

There are two audio capacitors:

```text
C_MIC_REC = mic recording cap
  +5V -> 220R -> 10k mic-drive pot -> BLACK_MIC -> C_MIC_REC -> MIC_TIP

C_SIDE = sidetone cap
  BLACK_MIC -> C_SIDE 470uF -> RV_SIDE -> 10R -> WHITE_RX
```

They are separate parts. Do not reuse one capacitor for both jobs.

The mic recording cap does not replace the mic-drive resistor or pot. The
carbon mic must still get its adjustable DC drive through `R_MIC_MIN` and
`RV_MIC`.

Resistors have no polarity.

Electrolytic capacitors do have polarity:

```text
long leg = +
short leg = -
stripe on capacitor body usually marks -
```

## Pot Pin Identification

Do not rely only on left/middle/right unless your pot is a standard 3-pin
inline style. Identify the pins with the meter first.

For any 3-pin pot:

```text
outer lug A -> outer lug B = fixed full value
middle/wiper -> either outer lug = changes as you turn the pot
```

To find the wiper:

1. Set the meter to ohms.
2. Measure between two pins.
3. Turn the pot.
4. The pair that changes includes the wiper.
5. The pair that stays fixed at the pot value is the two outer lugs.

For a standard inline pot viewed from the shaft/knob side with pins downward:

```text
outer A    wiper    outer B
  |          |        |
 left      middle    right
```

For a small blue trim pot, use the meter method. The physical middle-looking
pin is often the wiper, but not always in a way that is obvious on perfboard.

## Pot Wiring Pattern

Both audio pots are used as two-terminal variable resistors, also called
rheostats.

Use this pattern:

```text
input -> outer lug A
output -> wiper
outer lug B -> jumper to wiper
```

The wiper-to-outer jumper makes the circuit fail more gracefully if the wiper
briefly loses contact.

## Section 1: Receiver Playback

Solder:

```text
HP_TIP -> 220R -> WHITE_RX
HP_SLEEVE -> GND bus
RED_R_COMMON -> GND bus
```

Equivalent:

```text
ATR2x headphone tip ---- R_HP_SERIES 220R ---- WHITE_RX
ATR2x headphone sleeve ----------------------- RED_R_COMMON / GND
```

Cold checks:

```text
HP_TIP -> WHITE_RX: about 220 ohms
WHITE_RX -> GND: not shorted
HP_SLEEVE -> GND: near 0 ohms
RED_R_COMMON -> GND: near 0 ohms
WHITE_RX -> L1/K: no continuity
```

Power test:

```text
Play a quiet test tone.
Receiver should produce clear audio.
Ring, hook, and pulse should still work.
```

Stop here until receiver playback works.

## Section 2: Carbon Mic Drive And Recording

Solder the mic drive path:

```text
+5V bus -> R_MIC_MIN 220R -> RV_MIC 10k rheostat -> BLACK_MIC
RED_R_COMMON -> GND bus
```

Mic-drive pot wiring:

```text
R_MIC_MIN output -> RV_MIC outer lug A
RV_MIC wiper -> BLACK_MIC
RV_MIC outer lug B -> jumper to RV_MIC wiper
```

Solder the recording coupling cap:

```text
BLACK_MIC -> C_MIC_REC -> MIC_TIP
MIC_SLEEVE -> GND bus
```

If `C_MIC_REC` is electrolytic:

```text
C_MIC_REC + / long leg -> BLACK_MIC
C_MIC_REC - / short leg -> MIC_TIP
```

Why the polarity faces this way: `BLACK_MIC` carries the DC mic bias, while
`MIC_TIP` is the sound-card input side after AC coupling.

Cold checks:

```text
+5V -> BLACK_MIC: 220 ohms minimum, up to about 10.2k as RV_MIC turns
BLACK_MIC -> GND: not shorted
RED_R_COMMON -> GND: near 0 ohms
MIC_SLEEVE -> GND: near 0 ohms
MIC_TIP -> GND: not shorted
MIC_TIP -> BLACK_MIC: through C_MIC_REC, not DC shorted after cap charges
```

Starting pot position:

```text
Set RV_MIC near higher resistance first.
Then reduce resistance until recording level is strong but not clipping.
```

Power test:

```text
Record 5 seconds of speech.
Confirm the mic-drive pot changes level.
Normal speech should not clip.
Some carbon-mic hiss is expected.
```

Stop here until recording works.

## Section 3: Passive Hardware Sidetone

Solder:

```text
BLACK_MIC -> C_SIDE 470uF -> RV_SIDE 1k rheostat -> R_SIDE_MIN 10R -> WHITE_RX
```

Cap polarity:

```text
C_SIDE + / long leg -> BLACK_MIC
C_SIDE - / short leg -> RV_SIDE input
```

Sidetone pot wiring:

```text
C_SIDE - / short leg -> RV_SIDE outer lug A
RV_SIDE wiper -> R_SIDE_MIN 10R -> WHITE_RX
RV_SIDE outer lug B -> jumper to RV_SIDE wiper
```

Starting pot position:

```text
Set RV_SIDE to maximum resistance first.
That gives minimum sidetone.
Turn it down slowly by ear.
```

Cold checks:

```text
C_SIDE + -> BLACK_MIC: near 0 ohms
C_SIDE - -> RV_SIDE outer lug A: near 0 ohms
RV_SIDE wiper -> R_SIDE_MIN -> WHITE_RX: continuity through 10R plus pot setting
BLACK_MIC -> WHITE_RX: not a DC short; path is through C_SIDE and sidetone parts
WHITE_RX -> GND: not shorted
```

Power test:

```text
Speak into handset.
Your voice should be present in the receiver with no software delay.
Receiver playback should still be usable.
Mic recording should still be usable.
```

## Physical Layout Hints

- Keep the audio section away from `L1/K` and the Black Magic high-voltage
  output.
- Put the two pots where you can reach them after the board is mounted.
- Put `R_HP_SERIES` close to the `HP_TIP` or `WHITE_RX` path.
- Put `C_MIC_REC` close to `MIC_TIP`.
- Put `C_SIDE`, `RV_SIDE`, and `R_SIDE_MIN` near `BLACK_MIC` and `WHITE_RX`.
- Route `RED_R_COMMON`, `HP_SLEEVE`, and `MIC_SLEEVE` directly to the GND bus.
- Label the pots:

```text
MIC DRIVE 10k
SIDETONE 1k
```

## Full Audio Acceptance

After all three sections:

```text
Receiver playback works.
Mic recording works.
Mic-drive pot changes recording level.
Sidetone pot changes local voice level.
Ring still works.
Hook still reads GPIO17.
Dial still pulses GPIO22.
Pi remains stable.
```
