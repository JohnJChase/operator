# WE302 Rev A Perfboard Ringer Build Guide

Date: 2026-07-28

This guide builds only the ringer section on the permanent isolated-pad
perfboard. Build and test this section before adding hook, pulse, or audio.

Assumptions:

- The board is isolated-pad perfboard, not breadboard-pattern proto board.
- A Raspberry Pi female header is installed.
- A GND bus is installed on one end of the board.
- A +5V bus is installed on the other end of the board.
- Ring command uses BCM `GPIO6`, Raspberry Pi physical pin `31`.

If you temporarily use a different ring GPIO, only the GPIO header pin changes.
The relay, transistor, diode, and Black Magic wiring stays the same.

## Ringer Circuit Summary

```text
Pi GPIO6 -> 1k -> Q1 base
Q1 base -> 5.1k pulldown -> GND
Q1 emitter -> GND
Q1 collector -> relay coil low side

+5V -> relay coil high side
+5V -> relay COM contact
relay NO contact -> Black Magic +5V input
Black Magic GND input -> GND

Black Magic HV OUT1 -> L1
Black Magic HV OUT2 -> K
```

The relay switches the Black Magic low-voltage `+5V` input. It does not switch
the high-voltage ring output.

The Q1 base pulldown is part of the permanent build. It keeps the relay off
while the Pi boots, before software has configured GPIO6.

## Orientation Rules

Keep one board orientation while building. In this guide:

- Component side means the side with the relay, transistor, diode, and terminal
  blocks.
- Solder side means the underside of the board.
- Solder-side layouts are mirrored. Mark the important pins on the component
  side before flipping the board over.

## Raspberry Pi Header Pins

Current Rev A GPIO map:

```text
physical pin 2 or 4  = +5V
physical pin 6       = GND
physical pin 31      = GPIO6 ring command
```

Useful neighboring pins:

```text
physical pin 15 = GPIO22 dial pulse
physical pin 20 = GND
physical pin 31 = GPIO6 ring
```

## Relay Pin Guide

For the Axicom D2n / V23105 relay, use only four relay pins for Rev A:

```text
pin 1  = coil high side, +5V
pin 16 = coil low side, Q1 collector
pin 4  = contact COM, +5V
pin 8  = contact NO, Black Magic +5V input
```

Top/component-side guide with relay text readable:

```text
          Axicom D2n relay
          component-side view
          text readable, pins down

      pin 1                         pin 16
        o-----------------------------o
        |                             |
        |                             |
      pin 4                         pin 13
        o                             o

      pin 6                         pin 11
        o                             o

      pin 8                         pin 9
        o-----------------------------o
```

Solder side is mirrored. Do not use this top-view drawing directly while
soldering the underside unless you mentally flip it first.

Cold relay checks before wiring:

```text
pin 1 -> pin 16: about 160-170 ohms
pin 4 -> pin 6:  closed / near 0 ohms when relay is idle
pin 4 -> pin 8:  open when relay is idle
```

If pin `1 -> 16` reads 0 ohms, stop. The pins are shorted by board copper,
solder, or pin misidentification.

## Diode Orientation

The flyback diode goes across the relay coil.

```text
diode stripe / cathode -> +5V / relay pin 1
diode plain side       -> Q1 collector / relay pin 16
```

ASCII:

```text
+5V / relay pin 1 ----|<|---- relay pin 16 / Q1 collector
                      stripe
```

The stripe goes to +5V.

## Transistor Orientation

For the common PN2222A / 2N2222 plastic TO-92 package used in this build:

```text
flat face toward you, legs pointing down

    E   B   C
    |   |   |
  left mid right
```

Wire it as:

```text
E / emitter   -> GND bus
B / base      -> 1k resistor -> GPIO6 physical pin 31
C / collector -> relay pin 16
```

If the transistor marking is not PN2222A / 2N2222, confirm its pinout before
soldering. Some TO-92 NPNs use a different order.

## What Gets Wired Together

### +5V Node

These are intentionally tied together:

```text
+5V bus
relay pin 1
relay pin 4
diode stripe / cathode
```

### Relay Coil Low Node

This is a private node. It must not touch +5V or GND directly.

```text
relay pin 16
Q1 collector
diode plain side / anode
```

### Ring Command Node

```text
GPIO6 physical pin 31 -> 1k resistor -> Q1 base
Q1 base -> 5.1k pulldown -> GND bus
```

The 1k resistor has no polarity.

### Ground Node

These are intentionally tied together:

```text
GND bus
Q1 emitter
Q1 base pulldown resistor
Black Magic low-voltage GND input
```

### Switched Black Magic +5V

```text
relay pin 8 -> Black Magic low-voltage +5V input
```

Do not connect Black Magic `+5V` directly to the +5V bus. It receives +5V only
through relay pin 8 when the relay is energized.

### Black Magic Ring Output

Connect this only after the relay and Black Magic low-voltage switching test
correctly.

```text
Black Magic HV OUT1 -> phone L1
Black Magic HV OUT2 -> phone K
```

Neither high-voltage output lead connects to GND, +5V, GPIO, or audio.

## Build Order

### 1. Place The Relay

Before soldering the relay, verify:

```text
pin 1 -> pin 16: about 160-170 ohms
pin 4 -> pin 8:  open
```

Then mark pins `1`, `16`, `4`, and `8` on tape or with a paint pen.

### 2. Place Q1

Place Q1 near relay pin `16`.

```text
Q1 emitter   near GND bus
Q1 collector near relay pin 16
Q1 base      near the 1k GPIO resistor
```

### 3. Wire The Coil Driver

Solder:

```text
+5V bus -> relay pin 1
relay pin 16 -> Q1 collector
Q1 emitter -> GND bus
GPIO6 physical pin 31 -> 1k -> Q1 base
Q1 base -> 5.1k -> GND bus
```

Add the diode:

```text
diode stripe -> relay pin 1 / +5V
diode plain side -> relay pin 16 / Q1 collector
```

### 4. Cold Checks Before Power

With Pi power unplugged:

```text
+5V bus -> GND bus: not shorted
relay pin 1 -> +5V bus: near 0 ohms
relay pin 4 -> +5V bus: near 0 ohms, after pin 4 is wired
relay pin 16 -> +5V bus: not shorted
relay pin 16 -> GND bus: not shorted
relay pin 1 -> relay pin 16: about 160-170 ohms
GPIO6 -> +5V bus: not shorted
GPIO6 -> GND bus: not shorted
Q1 base -> GND bus: about 5.1k through the pulldown
```

Do not power the Pi until these pass.

### 5. First Power Test: Relay Only

Leave Black Magic disconnected for the first relay test.

Before running software, power the Pi with the board attached:

```text
relay should stay off
Q1 base should be near 0V
relay pin 16 / Q1 collector should be about 5V
```

Run on the Pi:

```python
from gpiozero import OutputDevice
from time import sleep

ring = OutputDevice(6, active_high=True, initial_value=False)

while True:
    ring.on()
    print("ring on")
    sleep(2)
    ring.off()
    print("ring off")
    sleep(2)
```

Expected meter readings:

```text
Q1 emitter to GND:
  idle: 0V
  ring: 0V

Q1 base to GND:
  idle: about 0V
  ring: about 0.6V to 0.8V

Q1 collector / relay pin 16 to GND:
  idle: about 5V
  ring: near 0V
```

Expected behavior:

```text
ring on  -> relay clicks
ring off -> relay releases
```

If the Pi reboots or shuts down, power off immediately and check for a +5V to
GND short.

### 6. Add Black Magic Low-Voltage Switching

After the relay clicks correctly, solder:

```text
relay pin 4 -> +5V bus
relay pin 8 -> Black Magic +5V input
Black Magic GND input -> GND bus
```

Test Black Magic input voltage:

```text
ring off: Black Magic +5V input to GND = 0V
ring on:  Black Magic +5V input to GND = about 5V
```

### 7. Add Black Magic High-Voltage Output

Only after the low-voltage switching test passes:

```text
Black Magic HV OUT1 -> L1
Black Magic HV OUT2 -> K
```

Final ringer acceptance:

```text
GPIO6 LOW: relay off, Black Magic unpowered, no ring
GPIO6 HIGH: relay clicks, Black Magic powered, bell rings
L1 -> GND: no continuity
K -> GND: no continuity
L1 -> +5V: no continuity
K -> +5V: no continuity
```

## Failure Clues

```text
Pi crashes when relay pin 16 is grounded:
  pin 16 is shorted to +5V or the wrong relay pin was identified.

Q1 base reaches 0.6-0.8V but no relay click:
  relay coil path, Q1 collector/emitter, diode, or relay pin identification is wrong.

Relay clicks but Black Magic input stays 0V:
  contact side is wrong; check relay pin 4 COM and pin 8 NO.

Black Magic input is always 5V:
  Black Magic +5V is bypassing the relay or relay NO/NC pin is wrong.

Bell rings continuously until the ring-test script starts:
  Q1 base is floating or being pulled high during boot. Install/check the
  5.1k base pulldown from Q1 base to GND.
```
