# Wolfpunk Weather Station

A three-element weather instrument for the Wolfpunk RP2040 pad controller.
Hold the weather in your left hand, play notes with your right, and the two
combine.

Sends MIDI over **USB** and over the **DIN (UART) socket** simultaneously.

```
   1 SUN     4   7          left hand : 1 2 3   weather modifiers
   2 WIND    5   8          right hand: 4 - 8   five scale degrees
   3 RAIN    6
```

## The idea

The three yellow keys are not eight presets — they are three **independent
transformations** that stack. Learn what each one does on its own and every
combination is predictable:

| Held | Weather | What it does |
|---|---|---|
| — | **Clear** | Plain sustained notes, one per key |
| SUN | **Sunshine** | Each note blooms into a shimmering chord an octave up; filter opens |
| WIND | **Breeze** | Gusts bend and wobble the pitch; a companion voice drifts around what you hold |
| RAIN | **Rain** | Every held key trickles droplets down the scale |
| SUN + WIND | **Heat Haze** | Shimmering chords with a deep, slow heat-wobble |
| SUN + RAIN | **Rainbow** | Droplets climb upward instead of falling, bright and airy |
| WIND + RAIN | **Squall** | Droplets scattered wide and fast — and thunder starts |
| all three | **Storm** | The whole system at once, thunder frequent |

Modifiers act **live**: pick one up halfway through a held note and that note
starts responding. Drop it and it settles back.

## Changing settings

**Tap** a yellow key on its own — no notes held, no other modifier down — and
it changes a setting instead of playing weather. Anything you play during the
hold cancels the tap, so this never fires by accident mid-phrase.

| Tap | Cycles | Matrix shows |
|---|---|---|
| SUN | Scale — Clear / Overcast / Frost / Monsoon / Aurora / Drought | vertical bars |
| WIND | Octave — C2 / C3 / C4 / C5 | horizontal bars |
| RAIN | Rain rate — Drizzle / Shower / Downpour | dashed rows |

**Hold all three for 1.2 s with nothing playing** for a panic: all notes off,
controllers reset, particles cleared. The matrix flashes an X.

All scales are five notes, so the five white keys always map cleanly and there
is no wrong note to find.

## MIDI channels

Assign a different sound to each channel — that is where most of the character
comes from.

| Ch | Role | Suggested sound |
|---|---|---|
| 1 | Main | lead or pad, needs sustain |
| 2 | Sun | bells, glass, bright plucks |
| 3 | Rain | short plucks, marimba, dripping water |
| 4 | Wind | airy, breathy pad |
| 5 | Thunder | sub bass, timpani, boom |

Continuous controllers: **CC1** mod (wind wobble), **CC74** brightness (sun
opens the filter), **CC91** reverb (rain wets it), **CC10** pan (wind sweep),
and **pitch bend** for gusts on channels 1, 2 and 4.

## Displays

- **8x8 matrix** — a live weather scene, not a readout. Horizon swell when
  clear, a sun with turning rays, wind streaks, rain that falls and splashes on
  the ground row, full-screen lightning. Your held keys mark their column on
  the ground.
- **NeoPixel** — the sky colour for the current weather, easing between states
  rather than cutting. Rainbow (sun + rain) keeps rotating through the hues.
- **Blue LED** — a barometer. Breathes faster and deeper as the weather builds,
  flares on every note and droplet, and goes full-bright for lightning.

## Hardware

VCC-GND Studio YD-RP2040, CircuitPython 9.2.8.

| | Pin |
|---|---|
| Keys 1-8 | GP26, GP21, GP22, GP20, GP18, GP17, GP19, GP16 |
| HT16K33 8x8 matrix | I2C, SCL GP1 / SDA GP0, address 0x70 |
| NeoPixel | GP23 |
| Blue LED | `board.LED` (PWM), falls back to GP25 |
| DIN MIDI out | UART TX GP4, 31250 baud |

Required libraries, from the CircuitPython bundle into `lib/` (not vendored
here): `adafruit_ht16k33`, `neopixel.mpy`.

## Key wiring

Keycap layout:

```
    1
2   4   7
3   5   8
    6
```

Keycap number to GPIO, **verified by measurement**, not inferred. This is the
soldered wiring and does not change — `PIN_MAP` is only the function
assignment and can be remapped freely.

| Keycap | GPIO | | Keycap | GPIO |
|---|---|---|---|---|
| 1 | GP26 | | 5 | GP18 |
| 2 | GP21 | | 6 | GP17 |
| 3 | GP22 | | 7 | GP19 |
| 4 | GP20 | | 8 | GP16 |

Same pattern as the Hieroglyph board.

To re-measure: set `DIAG = True` near the MIDI setup, deploy, and press the
keycaps in order — every press logs the GPIO behind it to the serial console.
Read it passively with:

```bash
python3 -c "import os,time; fd=os.open('/dev/cu.usbmodem2101',os.O_RDONLY); [print(os.read(fd,256).decode('utf8','replace'),end='') for _ in iter(int,1)]"
```

Do not infer this map from how the controls behave — on the Hieroglyph that
approach produced a plausible-looking map that went unquestioned for several
changes and had to be re-derived.

## Deploying

Copy `code.py` to the `CIRCUITPY` volume. There is no build step — the board
runs the file the moment it is saved, so a bad edit stops the music mid-note.
Keep this repo and the board in sync.

## Testing without the board

`tools/scene_test.py` stubs the CircuitPython modules, executes everything in
`code.py` above `while True:`, and drives the module directly — so the matrix
artwork, note routing, tap gating and stuck-note behaviour can all be checked
on a laptop.

```bash
python3 tools/scene_test.py code.py
```

It prints an ASCII dump of every weather scene, the MIDI channels each
combination touches, and asserts that nothing is left sounding after a full
release.

## Orientation

All artwork is written in logical coordinates (x = 0 left, y = 0 top) and any
correction for how the panel is mounted is applied once, at blit time, by the
`ROTATE` constant. If the scene comes out sideways or upside down, change
`ROTATE` — never the artwork.

`ROTATE = 0` is confirmed correct against the physical panel: rain falls
downward and splashes on the ground row.
