# code.py — Wolfpunk Weather Station
# =============================================================================
# A three-element weather instrument for the RP2040 pad controller.
#
#   LEFT HAND   yellow keys 1 2 3  =  SUN, WIND, RAIN   (momentary modifiers)
#   RIGHT HAND  white keys  4 - 8  =  five degrees of the current scale
#
# The three elements are ORTHOGONAL. Each one applies its own transformation,
# and they stack, so eight distinct weather systems fall out of three keys:
#
#   —            Clear      plain sustained notes, one per key
#   SUN          Sunshine   notes bloom into a shimmering chord, an octave up
#   WIND         Breeze     gusts bend and wobble the pitch; a drifting
#                           companion voice wanders around what you hold
#   RAIN         Rain       every held key trickles droplets down the scale
#   SUN + WIND   Heat Haze  shimmering chords with deep, slow heat-wobble
#   SUN + RAIN   Rainbow    droplets climb upward instead of falling
#   WIND + RAIN  Squall     droplets scattered wide, and thunder starts
#   ALL THREE    Storm      the whole system at once, thunder frequent
#
# TAP a yellow key ON ITS OWN (no notes, no other modifier) to change a
# setting rather than play:
#
#   tap SUN    cycle scale        Clear / Overcast / Frost / Monsoon /
#                                 Aurora / Drought        (vertical bars)
#   tap WIND   cycle octave       C2 / C3 / C4 / C5       (horizontal bars)
#   tap RAIN   cycle rain rate    Drizzle / Shower / Downpour  (dashes)
#
#   hold ALL THREE for 1.2s with no notes  ->  PANIC: all notes off, reset
#
# MIDI CHANNELS (assign a different sound to each for the full effect)
# -----------------------------------------------------------------------------
#   1  Main     what your right hand plays — lead or pad, needs sustain
#   2  Sun      harmony shimmer — bells, glass, bright plucks
#   3  Rain     droplets — short plucks, marimba, dripping water
#   4  Wind     drifting companion voice — airy pad, breathy
#   5  Thunder  low hits — sub bass, timpani, boom
#
# Continuous controllers: CC1 mod (wind wobble), CC74 brightness (sun),
# CC91 reverb (rain wetness), CC10 pan (wind sweep), pitch bend (gusts).
# Everything is sent to BOTH USB MIDI and the UART (DIN) MIDI out on GP4.
#
# DISPLAY
# -----------------------------------------------------------------------------
#   8x8 matrix  live weather scene: horizon, sun with turning rays, wind
#               streaks, falling rain that splashes on the ground row, and
#               full-screen lightning. Your held keys mark the ground.
#   NeoPixel    sky colour for the current weather (Rainbow cycles hue)
#   Blue LED    barometer: breathes faster as the weather builds, and flares
#               on every note, droplet and lightning strike
# =============================================================================

import time
import math
import random

import board
import busio
import digitalio
import usb_midi
import neopixel

try:
    import pwmio
except ImportError:
    pwmio = None

from adafruit_ht16k33.matrix import Matrix8x8

# ── Hardware ─────────────────────────────────────────────────────────────────

# Keycap number -> GPIO. VERIFIED BY MEASUREMENT 2026-08-15 with the DIAG
# block below, not inferred. This is the soldered wiring and does not change;
# the same pattern as the Hieroglyph board.
#
#         1
#     2   4   7
#     3   5   8
#         6
#
PIN_MAP = {
    1: "GP26", 2: "GP21", 3: "GP22", 4: "GP20",
    5: "GP18", 6: "GP17", 7: "GP19", 8: "GP16",
}

SUN, WIND, RAIN = 1, 2, 3
MODS = (SUN, WIND, RAIN)
NOTE_KEYS = (4, 5, 6, 7, 8)

i2c = busio.I2C(scl=board.GP1, sda=board.GP0, frequency=400000)
mx = Matrix8x8(i2c, address=0x70)
mx.auto_write = False          # batch a whole frame, then push it in one go
mx.brightness = 0.3
_mx_brightness = 0.3

pixel = neopixel.NeoPixel(board.GP23, 1, brightness=0.45, auto_write=False)

# Blue LED. PWM gives us a smooth barometer breath; fall back to on/off.
_led_pin = getattr(board, "LED", None)
if _led_pin is None:
    _led_pin = board.GP25

led_pwm = None
led_dig = None
if pwmio is not None:
    try:
        led_pwm = pwmio.PWMOut(_led_pin, frequency=1000, duty_cycle=0)
    except Exception:
        led_pwm = None
if led_pwm is None:
    led_dig = digitalio.DigitalInOut(_led_pin)
    led_dig.direction = digitalio.Direction.OUTPUT
    led_dig.value = False


def set_led(level):
    """level 0.0 - 1.0"""
    lv = 0.0 if level < 0.0 else (1.0 if level > 1.0 else level)
    if led_pwm is not None:
        # square it so the fade reads linearly to the eye
        led_pwm.duty_cycle = int(lv * lv * 65535)
    else:
        led_dig.value = lv > 0.4


buttons = {}
for _k, _p in PIN_MAP.items():
    _b = digitalio.DigitalInOut(getattr(board, _p))
    _b.direction = digitalio.Direction.INPUT
    _b.pull = digitalio.Pull.UP
    buttons[_k] = _b

# ── MIDI out (USB + DIN/UART) ────────────────────────────────────────────────

# ── Wiring diagnostic ────────────────────────────────────────────────────────
# PIN_MAP above is the FUNCTION assignment and can be remapped freely. Which
# GPIO sits behind each physical keycap is soldered and must be MEASURED, not
# inferred: set DIAG = True, deploy, press the keycaps in order and read the
# GPIO straight off the serial log.

DIAG = False

if DIAG:
    mx.fill(0)
    mx.show()
    print("")
    print("=" * 46)
    print("WIRING DIAGNOSTIC - press keycaps 1 to 8 in order")
    print("=" * 46)
    _seq = 0
    _prev = {s: True for s in PIN_MAP}
    while True:
        for _slot in PIN_MAP:
            _v = buttons[_slot].value
            if _v != _prev[_slot]:
                _prev[_slot] = _v
                if not _v:
                    _seq += 1
                    _down = [PIN_MAP[s] for s in sorted(PIN_MAP)
                             if not buttons[s].value]
                    print("press #%d  ->  %s   (down: %s)"
                          % (_seq, PIN_MAP[_slot], ",".join(_down)))
                    pixel[0] = (0, 120, 255)
                else:
                    print("             release %s" % PIN_MAP[_slot])
                    pixel[0] = (0, 0, 0)
                pixel.show()
        time.sleep(0.005)

usb_out = usb_midi.ports[1]
uart = busio.UART(tx=board.GP4, baudrate=31250)

# Channel constants are 0-based nibbles; the comment is the 1-based channel
# number you will see in your DAW or synth.
CH_MAIN = 0     # MIDI channel 1
CH_SUN = 1      # MIDI channel 2
CH_RAIN = 2     # MIDI channel 3
CH_WIND = 3     # MIDI channel 4
CH_THUNDER = 4  # MIDI channel 5
ALL_CH = (CH_MAIN, CH_SUN, CH_RAIN, CH_WIND, CH_THUNDER)


def _send(data):
    usb_out.write(data)
    uart.write(data)


def note_on(chan, note, vel=90):
    n = 0 if note < 0 else (127 if note > 127 else int(note))
    v = 1 if vel < 1 else (127 if vel > 127 else int(vel))
    _send(bytes([0x90 | chan, n, v]))


def note_off(chan, note):
    n = 0 if note < 0 else (127 if note > 127 else int(note))
    _send(bytes([0x80 | chan, n, 0]))


_cc_cache = {}


def cc(chan, num, val, force=False):
    v = 0 if val < 0 else (127 if val > 127 else int(val))
    key = (chan << 8) | num
    if not force and _cc_cache.get(key) == v:
        return
    _cc_cache[key] = v
    _send(bytes([0xB0 | chan, num, v]))


_bend_cache = {}


def bend(chan, value):
    """value 0 - 16383, 8192 = centre"""
    v = 0 if value < 0 else (16383 if value > 16383 else int(value))
    if abs(_bend_cache.get(chan, 8192) - v) < 24:
        return
    _bend_cache[chan] = v
    _send(bytes([0xE0 | chan, v & 0x7F, (v >> 7) & 0x7F]))


def midi_panic():
    for c in ALL_CH:
        cc(c, 123, 0, force=True)   # all notes off
        cc(c, 120, 0, force=True)   # all sound off
        cc(c, 1, 0, force=True)
        cc(c, 7, 100, force=True)
        cc(c, 10, 64, force=True)
        cc(c, 74, 64, force=True)
        cc(c, 91, 40, force=True)
        _bend_cache[c] = -1
        bend(c, 8192)

# ── Music ────────────────────────────────────────────────────────────────────

# Five-note scales so all five white keys always map cleanly.
SCALES = (
    ("Clear",    (0, 2, 4, 7, 9)),    # major pentatonic
    ("Overcast", (0, 3, 5, 7, 10)),   # minor pentatonic
    ("Frost",    (0, 2, 3, 7, 9)),    # kumoi
    ("Monsoon",  (0, 2, 3, 7, 8)),    # hirajoshi
    ("Aurora",   (0, 2, 4, 6, 9)),    # lydian pentatonic
    ("Drought",  (0, 1, 5, 7, 10)),   # in sen
)

OCTAVE_ROOTS = (36, 48, 60, 72)
OCTAVE_NAMES = ("C2", "C3", "C4", "C5")

RAIN_RATES = (("Drizzle", 0.42), ("Shower", 0.22), ("Downpour", 0.11))

cfg = {"scale": 0, "octave": 1, "rain": 1}

DEV = True   # set False to silence the serial log


def log(*a):
    if DEV:
        print(*a)


def scale_note(degree, oct_shift=0):
    steps = SCALES[cfg["scale"]][1]
    octaves, idx = divmod(int(degree), len(steps))
    n = OCTAVE_ROOTS[cfg["octave"]] + 12 * (octaves + oct_shift) + steps[idx]
    return 0 if n < 0 else (127 if n > 127 else n)

# ── State ────────────────────────────────────────────────────────────────────

w_sun = False
w_wind = False
w_rain = False

held = {}        # note key -> voice state
pending = []     # [fire_time, kind, chan, note, vel]  kind 0=on 1=off
PENDING_ON, PENDING_OFF = 0, 1

gust = 0.0
gust_target = 0.0
gust_next = 0.0

drops = []       # [x, y, vx, vy]   matrix rain particles
risers = []      # [x, y]           motes rising from held notes
splashes = []    # [x, until]
amb_next = 0.0
riser_next = 0.0
lightning = []   # [(start, end), ...]
thunder_ok_at = 0.0

led_energy = 0.0
pix_rgb = [8.0, 16.0, 40.0]
pix_pulse = 0.0

flash_rows = None
flash_until = 0.0
flash_rgb = None

# Per-modifier press tracking, so a lone tap can mean "change a setting"
# while a hold means "play the weather".
mod_state = {m: {"down": False, "t": 0.0, "solo": False, "used": False}
             for m in MODS}
last_raw = {k: True for k in buttons}

PAD_X = (0, 2, 3, 5, 7)   # matrix column for each of the five note keys

# ── Scheduler ────────────────────────────────────────────────────────────────


def schedule_on(t, chan, note, vel):
    pending.append([t, PENDING_ON, chan, note, vel])


def schedule_off(t, chan, note):
    pending.append([t, PENDING_OFF, chan, note, 0])


def cancel_pending_on(chan, note):
    for i in range(len(pending) - 1, -1, -1):
        p = pending[i]
        if p[1] == PENDING_ON and p[2] == chan and p[3] == note:
            pending.pop(i)


def update_pending(now):
    for i in range(len(pending) - 1, -1, -1):
        p = pending[i]
        if now >= p[0]:
            if p[1] == PENDING_ON:
                note_on(p[2], p[3], p[4])
            else:
                note_off(p[2], p[3])
            pending.pop(i)

# ── Weather engine ───────────────────────────────────────────────────────────


def weather_name():
    if w_sun and w_wind and w_rain:
        return "Storm"
    if w_wind and w_rain:
        return "Squall"
    if w_sun and w_rain:
        return "Rainbow"
    if w_sun and w_wind:
        return "Heat Haze"
    if w_rain:
        return "Rain"
    if w_wind:
        return "Breeze"
    if w_sun:
        return "Sunshine"
    return "Clear"


def spawn_drop(x, fast):
    if len(drops) > 26:
        return
    vy = (0.55 if not fast else 1.15) + random.uniform(0.0, 0.35)
    vx = (gust * random.uniform(0.25, 0.6)) if w_wind else 0.0
    drops.append([float(x), random.uniform(0.0, 0.9), vx, vy])


def strike_thunder(now):
    """A low hit plus a double lightning flash."""
    global thunder_ok_at, led_energy
    if now < thunder_ok_at:
        return
    thunder_ok_at = now + 2.2
    root = OCTAVE_ROOTS[cfg["octave"]] - 24
    n = scale_note(random.choice((0, 0, 4)), 0) - 24
    if n < 12:
        n = root if root >= 12 else 24
    note_on(CH_THUNDER, n, random.randint(105, 127))
    schedule_off(now + random.uniform(1.1, 1.9), CH_THUNDER, n)
    lightning.append((now, now + 0.07))
    lightning.append((now + 0.15, now + 0.24))
    led_energy = 1.0
    log("thunder", n)


def droplet(key, st, now):
    """One rain droplet from a held key. Shape depends on the other elements."""
    global led_energy
    i = st["drop_i"]

    if w_sun:
        # Rainbow: droplets climb, bright and airy.
        deg = st["deg"] + 2 + i
        osh = 1
        vel = int(min(120, 74 + i * 4) * random.uniform(0.85, 1.0))
        dur = 0.34
    elif w_wind:
        # Squall: scattered wide, thrown around.
        deg = st["deg"] + random.randint(-4, 5)
        osh = random.choice((-1, 0, 0, 1))
        vel = int(random.uniform(0.55, 1.0) * 118)
        dur = 0.16
    else:
        # Plain rain: a trickle down the scale, fading.
        deg = st["deg"] - i
        osh = 0
        vel = int(max(38, 104 - i * 8) * random.uniform(0.8, 1.0))
        dur = 0.30

    n = scale_note(deg, osh)
    note_on(CH_RAIN, n, vel)
    schedule_off(now + dur, CH_RAIN, n)
    spawn_drop(PAD_X[deg % 5], cfg["rain"] == 2 or w_wind)
    led_energy = max(led_energy, 0.45)

    if w_wind and w_rain and random.random() < (0.09 if w_sun else 0.05):
        strike_thunder(now)

    st["drop_i"] = (i + 1) % 10
    rate = RAIN_RATES[cfg["rain"]][1]
    jitter = 0.55 if w_wind else 0.22
    st["drop_next"] = now + rate * random.uniform(1.0 - jitter, 1.0 + jitter)


def spawn_wind_voice(st, now):
    n = scale_note(st["deg"] + 3, 1)
    note_on(CH_WIND, n, 68)
    st["wind"] = n
    st["wind_next"] = now + random.uniform(0.6, 1.6)


def kill_wind_voice(st):
    if st["wind"] is not None:
        note_off(CH_WIND, st["wind"])
        st["wind"] = None


def press_note(key, now):
    global led_energy, pix_pulse
    deg = NOTE_KEYS.index(key)
    n = scale_note(deg, 1 if w_sun else 0)
    note_on(CH_MAIN, n, 112 if w_sun else 96)

    st = {
        "deg": deg,
        "main": n,
        "sun": [],
        "wind": None,
        "wind_next": 0.0,
        "drop_next": (now if w_rain else None),
        "drop_i": 0,
    }

    if w_sun:
        # Chord blooms upward, voices staggered so it shimmers in.
        for k, (off, delay) in enumerate(((2, 0.05), (4, 0.13))):
            hn = scale_note(deg + off, 1)
            schedule_on(now + delay, CH_SUN, hn, 92 - k * 12)
            st["sun"].append(hn)

    if w_wind:
        spawn_wind_voice(st, now)

    held[key] = st
    led_energy = 1.0
    pix_pulse = 1.0


def release_note(key):
    st = held.pop(key, None)
    if st is None:
        return
    note_off(CH_MAIN, st["main"])
    for hn in st["sun"]:
        cancel_pending_on(CH_SUN, hn)
        note_off(CH_SUN, hn)
    kill_wind_voice(st)


def update_held(now):
    """Modifiers act live: pick one up mid-note and the held note responds."""
    for key in list(held.keys()):
        st = held.get(key)
        if st is None:
            continue

        # Wind companion voice
        if w_wind:
            if st["wind"] is None:
                spawn_wind_voice(st, now)
            elif now >= st["wind_next"]:
                nn = scale_note(st["deg"] + random.choice((2, 3, 4, 5, 7)), 1)
                if nn != st["wind"]:
                    note_on(CH_WIND, nn, int(52 + gust * 48))
                    note_off(CH_WIND, st["wind"])
                    st["wind"] = nn
                st["wind_next"] = now + random.uniform(0.3, 1.3) / (0.5 + gust)
        elif st["wind"] is not None:
            kill_wind_voice(st)

        # Rain droplets
        if w_rain:
            if st["drop_next"] is None:
                st["drop_next"] = now
                st["drop_i"] = 0
            elif now >= st["drop_next"]:
                droplet(key, st, now)
        elif st["drop_next"] is not None:
            st["drop_next"] = None


def update_wind(now):
    """Gusts drive pitch bend, mod wheel and pan — and the matrix streaks."""
    global gust, gust_target, gust_next

    if w_wind:
        if now >= gust_next:
            gust_target = random.uniform(0.08, 1.0)
            gust_next = now + random.uniform(0.7, 2.6)
        gust += (gust_target - gust) * 0.035
    else:
        gust += (0.0 - gust) * 0.06
        if gust < 0.005:
            gust = 0.0

    depth = 1500 if w_sun else 850     # heat haze bends further
    wob = math.sin(now * 2.3) * 0.62 + math.sin(now * 0.71) * 0.38
    b = 8192 + int(gust * depth * wob)
    for c in (CH_MAIN, CH_SUN, CH_WIND):
        bend(c, b)

    cc(CH_MAIN, 1, int(gust * 70))
    cc(CH_WIND, 1, int(gust * 110))
    cc(CH_WIND, 10, int(64 + math.sin(now * 0.9) * gust * 60))


def update_timbre():
    """Sun opens the filter, rain wets the reverb."""
    bright = 96 if w_sun else 58
    wet = 88 if w_rain else 34
    for c in (CH_MAIN, CH_SUN):
        cc(c, 74, bright)
    for c in (CH_MAIN, CH_RAIN):
        cc(c, 91, wet)

# ── Matrix graphics ──────────────────────────────────────────────────────────
#
# Drawing is done in LOGICAL coordinates: x = 0 is the left column, y = 0 is
# the top row, as the panel reads when the box is in front of you. Any
# correction for how the panel is mounted is applied once, at blit time, by
# ROTATE (0 / 90 / 180 / 270) — never by editing the artwork.
#
# ROTATE = 0 is CONFIRMED correct against the physical panel: rain falls
# downward and splashes on the ground row. The old firmware's 180 flip was
# wrong and turned the scene upside down. Do not change without a reason.

ROTATE = 0


def px(frame, x, y):
    if 0 <= x < 8 and 0 <= y < 8:
        frame[y] |= 1 << x


def blit(frame):
    mx.fill(0)
    for y in range(8):
        row = frame[y]
        if not row:
            continue
        for x in range(8):
            if (row >> x) & 1:
                if ROTATE == 0:
                    mx[x, y] = 1
                elif ROTATE == 90:
                    mx[y, 7 - x] = 1
                elif ROTATE == 180:
                    mx[7 - x, 7 - y] = 1
                else:
                    mx[7 - y, x] = 1
    mx.show()


def set_matrix_brightness(v):
    global _mx_brightness
    v = 0.05 if v < 0.05 else (1.0 if v > 1.0 else v)
    if abs(v - _mx_brightness) > 0.04:
        _mx_brightness = v
        mx.brightness = v


# Scene layers ---------------------------------------------------------------

wind_streaks = [[1, 0.0, 1.3], [3, 4.0, 0.85], [5, 8.0, 1.7]]  # y, x, speed


def draw_calm(frame, now):
    """Nothing held: a slow horizon swell and a couple of faint stars."""
    for x in range(8):
        y = 6 + int(round(math.sin(x * 0.85 + now * 0.7)))
        px(frame, x, 4 if y < 4 else (7 if y > 7 else y))
    if int(now * 1.7) % 3 == 0:
        px(frame, 1, 1)
    if int(now * 1.1) % 4 == 0:
        px(frame, 6, 2)


def draw_sun(frame, now):
    """A 2x2 core with eight rays turning around it and breathing in and out."""
    for dx in (0, 1):
        for dy in (0, 1):
            px(frame, 3 + dx, 2 + dy)
    r = 2.3 + 0.55 * math.sin(now * 2.0)
    phase = now * 1.1
    for k in range(8):
        a = phase + k * 0.7854
        px(frame,
           int(round(3.5 + math.cos(a) * r)),
           int(round(2.5 + math.sin(a) * r)))


def draw_wind(frame):
    for s in wind_streaks:
        s[1] = (s[1] + s[2] * (0.30 + gust * 1.5)) % 13.0
        x = int(s[1]) - 3
        for k in range(3):
            px(frame, x + k, s[0])


AMBIENT_RAIN = (0.30, 0.16, 0.08)   # spawn interval per rain rate


def update_particles(now):
    """Rain falls whenever RAIN is held, played or not — the display shows
    the weather you are holding, not only the notes you play."""
    global amb_next, riser_next

    if w_rain:
        if now >= amb_next:
            gap = AMBIENT_RAIN[cfg["rain"]]
            amb_next = now + gap * random.uniform(0.5, 1.5)
            spawn_drop(random.randint(0, 7), cfg["rain"] == 2 or w_wind)
    else:
        amb_next = now

    # Held notes send motes up when it is not raining, so plain Clear
    # weather still has something to watch.
    if held and not w_rain and now >= riser_next:
        riser_next = now + 0.30
        for st in held.values():
            if len(risers) < 16:
                risers.append([float(PAD_X[st["deg"]]), 6.5])

    for i in range(len(drops) - 1, -1, -1):
        d = drops[i]
        d[1] += d[3]
        d[0] += d[2]
        if d[0] < -1 or d[0] > 8 or d[1] >= 7.0:
            if 0 <= d[0] < 8 and len(splashes) < 12:
                splashes.append([int(d[0]), now + 0.16])
            drops.pop(i)

    for i in range(len(risers) - 1, -1, -1):
        r = risers[i]
        r[1] -= 0.55
        r[0] += gust * 0.22
        if r[1] < 0.0 or r[0] < -1 or r[0] > 8:
            risers.pop(i)

    for i in range(len(splashes) - 1, -1, -1):
        if now >= splashes[i][1]:
            splashes.pop(i)


def draw_particles(frame):
    for d in drops:
        px(frame, int(d[0]), int(d[1]))
    for r in risers:
        px(frame, int(r[0]), int(r[1]))
    for s in splashes:
        px(frame, s[0] - 1, 6)
        px(frame, s[0] + 1, 6)


def draw_ground(frame):
    for key, st in held.items():
        px(frame, PAD_X[st["deg"]], 7)


# Setting-change glyphs ------------------------------------------------------

def show_flash(rows, rgb, dur=0.7):
    global flash_rows, flash_until, flash_rgb
    flash_rows = rows
    flash_until = time.monotonic() + dur
    flash_rgb = rgb


def bars_v(n):
    """n filled columns from the left — used for the scale number."""
    mask = 0
    for i in range(n):
        mask |= 1 << i
    return [mask] * 8


def bars_h(n):
    """n filled rows from the bottom — used for the octave number."""
    rows = [0] * 8
    for i in range(n):
        rows[7 - i] = 0xFF
    return rows


def dashes(n):
    """n dashed rows — used for the rain rate."""
    rows = [0] * 8
    for i in range(n):
        rows[1 + i * 2] = 0x55
    return rows


GLYPH_PANIC = [0x81, 0x42, 0x24, 0x18, 0x18, 0x24, 0x42, 0x81]


def render_matrix(now):
    frame = [0] * 8

    lit = False
    for (a, b) in lightning:
        if a <= now < b:
            lit = True
            break

    if flash_rows is not None and now < flash_until:
        frame = list(flash_rows)
        set_matrix_brightness(0.55)
    elif lit:
        frame = [0xFF] * 8
        set_matrix_brightness(1.0)
    else:
        set_matrix_brightness(0.5 if w_sun else 0.3)
        if not (w_sun or w_wind or w_rain):
            draw_calm(frame, now)
        if w_sun:
            draw_sun(frame, now)
        if w_wind:
            draw_wind(frame)
        if drops or risers or splashes:
            draw_particles(frame)
        draw_ground(frame)

    blit(frame)

# ── NeoPixel: the sky ────────────────────────────────────────────────────────

SKY = {
    (False, False, False): (14, 34, 78),     # clear night blue
    (True,  False, False): (255, 168, 26),   # sunshine gold
    (False, True,  False): (26, 200, 168),   # breeze teal
    (False, False, True):  (18, 68, 210),    # rain blue
    (True,  True,  False): (255, 92, 40),    # heat haze orange-red
    (False, True,  True):  (48, 54, 150),    # squall slate blue
    (True,  True,  True):  (150, 28, 205),   # storm violet
    # (True, False, True) — Rainbow — is generated, see sky_target()
}


def hsv(h):
    """h 0.0-1.0 -> full-saturation rgb tuple"""
    i = int(h * 6.0) % 6
    f = h * 6.0 - int(h * 6.0)
    p, q, t = 0.0, 1.0 - f, f
    if i == 0:
        r, g, b = 1.0, t, p
    elif i == 1:
        r, g, b = q, 1.0, p
    elif i == 2:
        r, g, b = p, 1.0, t
    elif i == 3:
        r, g, b = p, q, 1.0
    elif i == 4:
        r, g, b = t, p, 1.0
    else:
        r, g, b = 1.0, p, q
    return (r * 255, g * 255, b * 255)


def sky_target(now):
    if w_sun and w_rain and not w_wind:
        return hsv((now * 0.14) % 1.0)          # Rainbow: hue keeps turning
    return SKY[(w_sun, w_wind, w_rain)]


def update_pixel(now):
    global pix_pulse

    lit = False
    for (a, b) in lightning:
        if a <= now < b:
            lit = True
            break

    if lit:
        pixel[0] = (255, 255, 255)
        pixel.show()
        return

    if flash_rgb is not None and now < flash_until:
        pixel[0] = flash_rgb
        pixel.show()
        return

    tgt = sky_target(now)
    for i in range(3):
        pix_rgb[i] += (tgt[i] - pix_rgb[i]) * 0.09   # weather changes, not cuts

    pix_pulse *= 0.88
    breath = 0.80 + 0.14 * math.sin(now * 1.4) + pix_pulse * 0.55
    if breath > 1.6:
        breath = 1.6

    pixel[0] = (min(255, int(pix_rgb[0] * breath)),
                min(255, int(pix_rgb[1] * breath)),
                min(255, int(pix_rgb[2] * breath)))
    pixel.show()

# ── Blue LED: the barometer ──────────────────────────────────────────────────


def update_blue_led(now):
    global led_energy

    for (a, b) in lightning:
        if a <= now < b:
            set_led(1.0)
            return

    intensity = (1 if w_sun else 0) + (1 if w_wind else 0) + (1 if w_rain else 0)
    rate = 0.55 + intensity * 0.85          # builds as the weather builds
    depth = 0.06 + intensity * 0.05
    breath = 0.07 + depth * (0.5 + 0.5 * math.sin(now * rate * 2.0))

    led_energy *= 0.90
    if led_energy < 0.01:
        led_energy = 0.0

    set_led(breath + led_energy)

# ── Settings & panic ─────────────────────────────────────────────────────────


def cycle_setting(m):
    if m == SUN:
        cfg["scale"] = (cfg["scale"] + 1) % len(SCALES)
        show_flash(bars_v(cfg["scale"] + 1), (0, 255, 90))
        log("Scale  ->", SCALES[cfg["scale"]][0])
    elif m == WIND:
        cfg["octave"] = (cfg["octave"] + 1) % len(OCTAVE_ROOTS)
        show_flash(bars_h(cfg["octave"] + 1), (255, 170, 0))
        log("Octave ->", OCTAVE_NAMES[cfg["octave"]])
    else:
        cfg["rain"] = (cfg["rain"] + 1) % len(RAIN_RATES)
        show_flash(dashes(cfg["rain"] + 1), (0, 120, 255))
        log("Rain   ->", RAIN_RATES[cfg["rain"]][0])


def do_panic():
    global gust, gust_target, led_energy
    for key in list(held.keys()):
        release_note(key)
    del pending[:]
    del drops[:]
    del risers[:]
    del splashes[:]
    del lightning[:]
    midi_panic()
    gust = 0.0
    gust_target = 0.0
    led_energy = 0.0
    show_flash(GLYPH_PANIC, (255, 0, 0), 0.9)
    log("PANIC - all notes off, clear skies")

# ── Input ────────────────────────────────────────────────────────────────────


panic_armed = True


def scan_inputs(now):
    global w_sun, w_wind, w_rain, panic_armed

    # Note keys first: a note played during a modifier hold cancels that
    # modifier's tap-to-change action.
    for k in NOTE_KEYS:
        v = buttons[k].value
        if v != last_raw[k]:
            last_raw[k] = v
            if not v:
                for m in MODS:
                    if mod_state[m]["down"]:
                        mod_state[m]["used"] = True
                press_note(k, now)
            else:
                release_note(k)

    for m in MODS:
        v = buttons[m].value
        ms = mod_state[m]
        if v != last_raw[m]:
            last_raw[m] = v
            if not v:
                others = [o for o in MODS if o != m and mod_state[o]["down"]]
                ms["down"] = True
                ms["t"] = now
                ms["used"] = bool(held)
                ms["solo"] = not others
                for o in others:
                    mod_state[o]["solo"] = False
            else:
                ms["down"] = False
                if (ms["solo"] and not ms["used"]
                        and not held and (now - ms["t"]) < 0.7):
                    cycle_setting(m)

    w_sun = mod_state[SUN]["down"]
    w_wind = mod_state[WIND]["down"]
    w_rain = mod_state[RAIN]["down"]

    # All three held, nothing played -> panic
    if w_sun and w_wind and w_rain:
        if (panic_armed and not held
                and not any(mod_state[m]["used"] for m in MODS)
                and now - max(mod_state[m]["t"] for m in MODS) > 1.2):
            panic_armed = False
            do_panic()
    elif not (w_sun or w_wind or w_rain):
        panic_armed = True

# ── Boot ─────────────────────────────────────────────────────────────────────


def boot_animation():
    """A sunrise, so you know the box is awake."""
    t0 = time.monotonic()
    while True:
        t = time.monotonic() - t0
        if t > 1.5:
            break
        frame = [0] * 8
        cy = 9.0 - t * 5.0
        for dx in (0, 1):
            for dy in (0, 1):
                px(frame, 3 + dx, int(cy) + dy)
        if t > 0.7:
            r = 2.4
            for k in range(8):
                a = t * 4.0 + k * 0.7854
                px(frame,
                   int(round(3.5 + math.cos(a) * r)),
                   int(round(cy + 0.5 + math.sin(a) * r)))
        px(frame, 0, 7)
        px(frame, 2, 7)
        px(frame, 3, 7)
        px(frame, 5, 7)
        px(frame, 7, 7)
        blit(frame)
        k = t / 1.5
        pixel[0] = (int(14 + 240 * k), int(34 + 134 * k), int(78 - 52 * k))
        pixel.show()
        set_led(k * 0.5)
        time.sleep(0.03)
    set_led(0.0)


midi_panic()
boot_animation()

print("-" * 58)
print("Wolfpunk Weather Station")
print("  1 SUN   2 WIND   3 RAIN      (hold with the left hand)")
print("  4 - 8   notes                (play with the right hand)")
print("  tap a lone modifier to cycle scale / octave / rain rate")
print("  hold all three for 1.2s to panic")
print("  MIDI ch 1 main  2 sun  3 rain  4 wind  5 thunder")
print("  Scale {}   Octave {}   Rain {}".format(
    SCALES[cfg["scale"]][0], OCTAVE_NAMES[cfg["octave"]],
    RAIN_RATES[cfg["rain"]][0]))
print("-" * 58)

# ── Main loop ────────────────────────────────────────────────────────────────

t_gfx = 0.0
t_ctrl = 0.0
t_led = 0.0
last_weather = ""

while True:
    now = time.monotonic()

    scan_inputs(now)
    update_pending(now)
    update_held(now)

    if now - t_ctrl >= 0.033:
        t_ctrl = now
        update_wind(now)
        update_timbre()

    if now - t_gfx >= 0.045:
        t_gfx = now
        update_particles(now)
        render_matrix(now)
        update_pixel(now)
        if lightning and now > lightning[-1][1]:
            del lightning[:]

    if now - t_led >= 0.02:
        t_led = now
        update_blue_led(now)

    wn = weather_name()
    if wn != last_weather:
        last_weather = wn
        log("weather:", wn)

    time.sleep(0.002)
