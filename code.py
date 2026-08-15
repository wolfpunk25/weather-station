# tidal.py — Ambient Voice Field
# ─────────────────────────────────────────────────────────────────────────────
# Six independent sustained voices, each on its own MIDI channel (1-6).
# Voices wake slowly, drift between notes, and sleep again — with or without
# you. Hold Shift (btn 7) for deeper controls. See CONTROLS below.
#
# CONTROLS
# ────────────────────────────────────────────────────────────────────────────
# Buttons 1-6 : Toggle voice on / off  (wakes slowly, releases slowly)
# Button 7    : [tap] Cycle scale   [hold] Shift key for combos below
# Button 8    : [short] Start Breathe  [mid-breathe: reverse it]
#               [long >0.5s] Panic reset
#
# Shift + 1   : Root note  ↓ one octave
# Shift + 2   : Root note  ↑ one octave
# Shift + 3   : Cycle Gravity mode  (Float → Root Pull → Cluster → Spread)
# Shift + 4   : Cycle Drift speed   (Glacial → Medium → Restless)
# Shift + 5   : Scatter  (nudge all active voices to random new scale tones)
# Shift + 6   : Release all  (gracefully fades everything to silence)
# Shift + 8   : Outro  (all voices begin a very long ~50s fade to silence)
#
# GRAVITY MODES
# ────────────────────────────────────────────────────────────────────────────
# Float      — voices wander freely: random ±1-2 scale steps
# Root Pull  — voices gravitate back toward root and fifth
# Cluster    — voices drift toward each other (density builds)
# Spread     — voices drift apart (harmony opens up)
#
# MIDI
# ────────────────────────────────────────────────────────────────────────────
# CC 11 (Expression) — per-voice dynamic envelopes (attack / release)
# CC  1 (Mod Wheel)  — slow sinusoidal modulation per voice for timbre movement
# CC  7 (Volume)     — global swell during Breathe
# Channels 1-6 (voices 0-5) — assign a slow-attack pad preset to each
# ─────────────────────────────────────────────────────────────────────────────

import time
import math
import random
import board
import busio
import digitalio
import usb_midi
import neopixel
from adafruit_ht16k33.matrix import Matrix8x8

# ── Hardware ──────────────────────────────────────────────────────────────────

PIN_MAP = {
    1: "GP26", 2: "GP21", 3: "GP20", 4: "GP19",
    5: "GP22", 6: "GP18", 7: "GP16", 8: "GP17",
}

i2c = busio.I2C(scl=board.GP1, sda=board.GP0, frequency=400000)
mx  = Matrix8x8(i2c, address=0x70)
mx.brightness = 0.3

pixel = neopixel.NeoPixel(board.GP23, 1, brightness=0.15)

try:
    onboard_led = digitalio.DigitalInOut(board.LED)
except AttributeError:
    onboard_led = digitalio.DigitalInOut(board.GP25)
onboard_led.direction = digitalio.Direction.OUTPUT
onboard_led.value = False

buttons = {}
for _k, _p in PIN_MAP.items():
    _b = digitalio.DigitalInOut(getattr(board, _p))
    _b.direction = digitalio.Direction.INPUT
    _b.pull      = digitalio.Pull.UP
    buttons[_k]  = _b

usb_out = usb_midi.ports[1]
uart    = busio.UART(tx=board.GP4, baudrate=31250)

# ── MIDI Channel Helper ───────────────────────────────────────────────────────

def ch(v_idx):
    """Convert 0-based voice index to 1-based MIDI channel."""
    return v_idx + 1

# ── Raw MIDI ──────────────────────────────────────────────────────────────────

def raw_note_on(channel, note, vel=80):
    b = bytes([0x90 | (channel & 0xF), note & 0x7F, vel & 0x7F])
    usb_out.write(b)
    uart.write(b)

def raw_note_off(channel, note):
    b = bytes([0x80 | (channel & 0xF), note & 0x7F, 0])
    usb_out.write(b)
    uart.write(b)

def raw_cc(channel, cc, val):
    b = bytes([0xB0 | (channel & 0xF), cc & 0x7F, int(val) & 0x7F])
    usb_out.write(b)
    uart.write(b)

def midi_panic():
    """Silence everything and reset controllers on channels 1–6."""
    for _ch in range(1, 7):
        raw_cc(_ch, 123, 0)   # all notes off
        raw_cc(_ch,   7, 100) # volume back to default
        raw_cc(_ch,  11, 0)   # expression to 0
        raw_cc(_ch,   1, 0)   # mod wheel to 0

# ── Music Theory ──────────────────────────────────────────────────────────────

DEFAULT_ROOT = 48  # C3

SCALES = [
    {"name": "Pentatonic", "notes": [0, 3,  5,  7, 10      ], "color": (255, 180,   0), "letter": "p"},
    {"name": "Major",      "notes": [0, 2,  4,  5,  7, 9, 11], "color": (  0, 220,   0), "letter": "m"},
    {"name": "Minor",      "notes": [0, 2,  3,  5,  7, 8, 10], "color": (200,  30,  30), "letter": "n"},
    {"name": "Dorian",     "notes": [0, 2,  3,  5,  7, 9, 10], "color": (  0,  80, 255), "letter": "d"},
    {"name": "Lydian",     "notes": [0, 2,  4,  6,  7, 9, 11], "color": (160,   0, 255), "letter": "l"},
    {"name": "Mixolydian", "notes": [0, 2,  4,  5,  7, 9, 10], "color": (255, 120,   0), "letter": "x"},
]

GRAVITY_NAMES = ["Float", "Root Pull", "Cluster", "Spread"]
GRAV_ICONS    = ["grav_float", "grav_root", "grav_cluster", "grav_spread"]

DRIFT_SPEEDS      = [45.0, 18.0, 7.0]
DRIFT_SPEED_NAMES = ["Glacial", "Medium", "Restless"]
DRIFT_ICONS       = ["spd_slow", "spd_mid", "spd_fast"]

# Voice lifecycle timing (seconds)
ATK_MIN,  ATK_MAX  = 3.0, 8.0   # attack ramp
REL_MIN,  REL_MAX  = 5.0, 12.0  # normal release
OUTRO_DUR          = 50.0        # outro release duration
BREATHE_PEAK       = 14.0        # time to swell peak
BREATHE_DUR        = 30.0        # total breathe cycle
BREATHE_REV_DUR    = 9.0         # time to fade when reversed

# Voice states
SLEEPING  = 0
WAKING    = 1
ACTIVE    = 2
RELEASING = 3

# ── App State ─────────────────────────────────────────────────────────────────

cfg = {
    "root":        DEFAULT_ROOT,
    "scale":       0,
    "gravity":     0,
    "drift_speed": 1,
}

def new_voice(i):
    """Create a fresh sleeping voice for index i (0-5)."""
    return {
        "state":      SLEEPING,
        "note":       None,
        "degree":     i % 5,
        "expr":       0.0,
        "state_t":    0.0,
        "atk":        ATK_MIN,
        "rel":        REL_MIN,
        "drift_next": 0.0,
        "mod_ph":     random.uniform(0.0, 6.28),
        "last_cc11":  -1,
        "last_cc1":   -1,
        "rel_start_expr": 1.0,
    }

voices = [new_voice(i) for i in range(6)]

# Neopixel
pix_ph = 0.0

# Breathe
breathe_on    = False
breathe_t0    = 0.0
breathe_rev   = False
breathe_rev_t = 0.0
breathe_cc7   = [100] * 6

# Scatter flash
scatter_flash_until = 0.0

# Non-blocking matrix flash
flash_bitmap = None
flash_until  = 0.0

# Input state
shift_on   = False
shift_used = False
btn8_t     = None
last_btn   = {k: True for k in buttons}

# ── 8×8 Bitmaps ───────────────────────────────────────────────────────────────

BITMAPS = {
    "p": [0x3C, 0x42, 0x42, 0x7C, 0x40, 0x40, 0x40, 0x40],
    "m": [0x42, 0x66, 0x5A, 0x42, 0x42, 0x42, 0x42, 0x42],
    "n": [0x42, 0x62, 0x52, 0x4A, 0x46, 0x42, 0x42, 0x42],
    "d": [0x7C, 0x42, 0x42, 0x42, 0x42, 0x42, 0x42, 0x7C],
    "l": [0x40, 0x40, 0x40, 0x40, 0x40, 0x40, 0x40, 0x7E],
    "x": [0x42, 0x42, 0x24, 0x18, 0x18, 0x24, 0x42, 0x42],

    "grav_float":   [0x00, 0x18, 0x3C, 0x66, 0x66, 0x3C, 0x18, 0x00],
    "grav_root":    [0x00, 0x18, 0x18, 0xFF, 0xFF, 0x18, 0x18, 0x00],
    "grav_cluster": [0x00, 0x3C, 0x7E, 0xFF, 0xFF, 0x7E, 0x3C, 0x00],
    "grav_spread":  [0x81, 0x42, 0x24, 0x18, 0x18, 0x24, 0x42, 0x81],

    "spd_slow":     [0x00, 0x00, 0x00, 0x7E, 0x7E, 0x00, 0x00, 0x00],
    "spd_mid":      [0x00, 0x18, 0x3C, 0xFF, 0xFF, 0x3C, 0x18, 0x00],
    "spd_fast":     [0xFF, 0xBD, 0xFF, 0x24, 0x24, 0xFF, 0xBD, 0xFF],

    "oct_down":     [0x00, 0x18, 0x3C, 0x7E, 0x18, 0x18, 0x18, 0x00],
    "oct_up":       [0x00, 0x18, 0x18, 0x18, 0x7E, 0x3C, 0x18, 0x00],

    "scatter":      [0x42, 0x24, 0x99, 0x3C, 0x3C, 0x99, 0x24, 0x42],
    "release_all":  [0x00, 0x3C, 0x42, 0x81, 0x81, 0x42, 0x3C, 0x00],
    "panic":        [0xFF, 0x81, 0xBD, 0xA5, 0xA5, 0xBD, 0x81, 0xFF],
    "breathe":      [0x18, 0x24, 0x42, 0x81, 0x81, 0x42, 0x24, 0x18],
}

def flash(key, dur=0.7):
    """Queue a non-blocking matrix flash (letter or icon)."""
    global flash_bitmap, flash_until
    data = BITMAPS.get(key)
    if data:
        flash_bitmap = data
        flash_until  = time.monotonic() + dur

# ── Throttled CC Helpers ──────────────────────────────────────────────────────

CC_THRESHOLD = 2

def send_cc11(v, v_idx):
    val = int(v["expr"] * 127)
    if abs(val - v["last_cc11"]) >= CC_THRESHOLD or v["last_cc11"] < 0:
        raw_cc(ch(v_idx), 11, val)
        v["last_cc11"] = val

def send_cc1(v, v_idx, val):
    if abs(val - v["last_cc1"]) >= CC_THRESHOLD or v["last_cc1"] < 0:
        raw_cc(ch(v_idx), 1, val)
        v["last_cc1"] = val

# ── Note Helpers ──────────────────────────────────────────────────────────────

def get_note(v_idx, degree):
    sc   = SCALES[cfg["scale"]]["notes"]
    note = cfg["root"] + sc[degree % len(sc)]
    note += 12 if v_idx >= 3 else 0
    return min(127, note)

def next_degree(v_idx):
    g    = cfg["gravity"]
    sc   = SCALES[cfg["scale"]]["notes"]
    s    = len(sc)
    cur  = voices[v_idx]["degree"]

    if g == 0:
        step = random.choice([-2, -1, -1, 1, 1, 2])
        return max(0, min(s - 1, cur + step))

    elif g == 1:
        fifth = min(s - 1, 4)
        pool  = [0, 0, 0, fifth, fifth, max(0, cur - 1), min(s - 1, cur + 1)]
        return random.choice(pool)

    elif g == 2:
        others = [voices[j]["degree"] for j in range(6)
                  if j != v_idx and voices[j]["state"] in (WAKING, ACTIVE)]
        if others:
            avg  = sum(others) / len(others)
            step = 1 if avg > cur else (-1 if avg < cur else random.choice([-1, 1]))
        else:
            step = random.choice([-1, 1])
        return max(0, min(s - 1, cur + step))

    else:
        others = [voices[j]["degree"] for j in range(6)
                  if j != v_idx and voices[j]["state"] in (WAKING, ACTIVE)]
        if others:
            avg  = sum(others) / len(others)
            step = -1 if avg > cur else (1 if avg < cur else random.choice([-1, 1]))
        else:
            step = random.choice([-1, 1])
        return max(0, min(s - 1, cur + step))

# ── Voice Control ─────────────────────────────────────────────────────────────

def wake(v_idx, now):
    v    = voices[v_idx]
    note = get_note(v_idx, v["degree"])
    raw_cc(ch(v_idx), 11, 0)
    raw_cc(ch(v_idx),  7, 100)
    raw_note_on(ch(v_idx), note, 80)
    v["state"]      = WAKING
    v["note"]       = note
    v["expr"]       = 0.0
    v["last_cc11"]  = 0
    v["last_cc1"]   = -1
    v["state_t"]    = now
    v["atk"]        = random.uniform(ATK_MIN, ATK_MAX)
    spd             = DRIFT_SPEEDS[cfg["drift_speed"]]
    v["drift_next"] = now + random.uniform(spd * 0.8, spd * 1.2)
    print(f"Voice {v_idx + 1} → WAKING  ch={ch(v_idx)}  note={note}")

def begin_release(v_idx, now, dur=None):
    v = voices[v_idx]
    if v["state"] == SLEEPING:
        return
    v["state"]          = RELEASING
    v["state_t"]        = now
    v["rel"]            = dur if dur is not None else random.uniform(REL_MIN, REL_MAX)
    v["rel_start_expr"] = v["expr"]
    print(f"Voice {v_idx + 1} → RELEASING  ch={ch(v_idx)}  dur={v['rel']:.1f}s")

def toggle(v_idx, now):
    v = voices[v_idx]
    if v["state"] == SLEEPING:
        wake(v_idx, now)
    elif v["state"] in (WAKING, ACTIVE):
        begin_release(v_idx, now)

def drift(v_idx, now):
    v        = voices[v_idx]
    new_deg  = next_degree(v_idx)
    new_note = get_note(v_idx, new_deg)
    if new_note != v["note"]:
        raw_note_on(ch(v_idx), new_note, 80)
        if v["note"] is not None:
            raw_note_off(ch(v_idx), v["note"])
        v["note"]   = new_note
        v["degree"] = new_deg
        print(f"Voice {v_idx + 1} drifted → ch={ch(v_idx)}  note={new_note}")
    spd             = DRIFT_SPEEDS[cfg["drift_speed"]]
    v["drift_next"] = now + random.uniform(spd * 0.7, spd * 1.3)

def do_scatter(now):
    global scatter_flash_until
    sc = SCALES[cfg["scale"]]["notes"]
    for i, v in enumerate(voices):
        if v["state"] in (WAKING, ACTIVE):
            nd = random.randint(0, len(sc) - 1)
            nn = get_note(i, nd)
            if nn != v["note"]:
                raw_note_on(ch(i), nn, 80)
                if v["note"] is not None:
                    raw_note_off(ch(i), v["note"])
                v["note"]   = nn
                v["degree"] = nd
    scatter_flash_until = now + 0.2
    flash("scatter", 0.5)
    print("Scatter!")

def rekey_active_voices():
    for i, v in enumerate(voices):
        if v["state"] in (WAKING, ACTIVE):
            new_note = get_note(i, v["degree"])
            if new_note != v["note"]:
                raw_note_on(ch(i), new_note, 80)
                if v["note"] is not None:
                    raw_note_off(ch(i), v["note"])
                v["note"] = new_note

# ── Envelope Updates ──────────────────────────────────────────────────────────

def update_voices(now):
    for i, v in enumerate(voices):

        if v["state"] == WAKING:
            t = min(1.0, (now - v["state_t"]) / v["atk"])
            v["expr"] = t
            send_cc11(v, i)
            if t >= 1.0:
                v["state"]   = ACTIVE
                v["state_t"] = now
                print(f"Voice {i + 1} → ACTIVE  ch={ch(i)}")

        elif v["state"] == ACTIVE:
            v["mod_ph"] = (v["mod_ph"] + 0.003) % 6.28
            mod_val = int((math.sin(v["mod_ph"]) * 0.5 + 0.5) * 35)
            send_cc1(v, i, mod_val)
            if now >= v["drift_next"]:
                drift(i, now)

        elif v["state"] == RELEASING:
            t = max(0.0, 1.0 - (now - v["state_t"]) / v["rel"])
            v["expr"] = v["rel_start_expr"] * t
            send_cc11(v, i)
            if t <= 0.0:
                if v["note"] is not None:
                    raw_note_off(ch(i), v["note"])
                raw_cc(ch(i), 1, 0)
                v["last_cc1"]  = 0
                v["last_cc11"] = 0
                v["state"] = SLEEPING
                v["note"]  = None
                v["expr"]  = 0.0
                print(f"Voice {i + 1} → SLEEPING  ch={ch(i)}")

# ── Breathe ───────────────────────────────────────────────────────────────────

def update_breathe(now):
    global breathe_on, breathe_cc7

    if not breathe_on:
        return

    elapsed = now - breathe_t0

    if breathe_rev:
        rev_elapsed = now - breathe_rev_t
        mult = max(0.0, 1.0 - rev_elapsed / BREATHE_REV_DUR)
        if mult <= 0.0:
            breathe_on = False
            for _i in range(6):
                if voices[_i]["state"] != SLEEPING:
                    raw_cc(ch(_i), 7, 100)
                    breathe_cc7[_i] = 100
            return
    else:
        if elapsed < BREATHE_PEAK:
            mult = elapsed / BREATHE_PEAK
        elif elapsed < BREATHE_DUR:
            mult = 1.0 - (elapsed - BREATHE_PEAK) / (BREATHE_DUR - BREATHE_PEAK)
        else:
            breathe_on = False
            for _i in range(6):
                if voices[_i]["state"] != SLEEPING:
                    raw_cc(ch(_i), 7, 100)
                    breathe_cc7[_i] = 100
            return

    vol = int(80 + mult * 47)
    for i, v in enumerate(voices):
        if v["state"] != SLEEPING:
            if abs(vol - breathe_cc7[i]) >= 2:
                raw_cc(ch(i), 7, vol)
                breathe_cc7[i] = vol

# ── Matrix Display ────────────────────────────────────────────────────────────

_last_mx_t   = 0.0
MATRIX_RATE  = 0.08

def render_matrix(now):
    global _last_mx_t, flash_bitmap, flash_until

    if now - _last_mx_t < MATRIX_RATE:
        return
    _last_mx_t = now

    mx.fill(0)

    if flash_bitmap is not None and now < flash_until:
        for row_i, byte_v in enumerate(flash_bitmap):
            for col_i in range(8):
                if (byte_v >> (7 - col_i)) & 1:
                    mx[7 - col_i, 7 - row_i] = 1
        mx.show()
        return
    else:
        flash_bitmap = None

    sc_len = len(SCALES[cfg["scale"]]["notes"])
    for i, v in enumerate(voices):
        if v["state"] == SLEEPING:
            continue
        if v["state"] == RELEASING and int(now * 6) % 2 == 0:
            continue

        col = i + 1
        row = int(v["degree"] / max(1, sc_len - 1) * 6)
        mx[7 - col, 7 - row] = 1

    mx.show()

# ── Neopixel ──────────────────────────────────────────────────────────────────

def update_pixel(now):
    global pix_ph

    if shift_on:
        pixel[0] = (180, 180, 180)
        return

    if now < scatter_flash_until:
        pixel[0] = (255, 255, 255)
        return

    sc_col   = SCALES[cfg["scale"]]["color"]
    active_n = sum(1 for v in voices if v["state"] != SLEEPING)

    breathe_glow = 0.0
    if breathe_on:
        elapsed = now - breathe_t0
        if not breathe_rev:
            if elapsed < BREATHE_PEAK:
                breathe_glow = (elapsed / BREATHE_PEAK) * 0.45
            elif elapsed < BREATHE_DUR:
                breathe_glow = max(0.0, 0.45 - (elapsed - BREATHE_PEAK) /
                                   (BREATHE_DUR - BREATHE_PEAK) * 0.45)

    pix_ph = (pix_ph + 0.004) % 6.28
    pulse  = 0.85 + 0.15 * math.sin(pix_ph)

    base = (0.06 + (active_n / 6.0) * 0.94) * pulse * (1.0 + breathe_glow)
    base = min(1.0, base)

    pixel[0] = (int(sc_col[0] * base), int(sc_col[1] * base), int(sc_col[2] * base))

# ── Onboard LED ───────────────────────────────────────────────────────────────

def update_onboard_led():
    onboard_led.value = any(v["state"] != SLEEPING for v in voices)

# ── Startup ───────────────────────────────────────────────────────────────────

for _ch in range(1, 7):
    raw_cc(_ch, 7, 100)

print("─" * 50)
print("Tidal — Ambient Voice Field")
print(f"Scale:   {SCALES[cfg['scale']]['name']}")
print(f"Gravity: {GRAVITY_NAMES[cfg['gravity']]}")
print(f"Drift:   {DRIFT_SPEED_NAMES[cfg['drift_speed']]}")
print(f"Root:    {cfg['root']}")
print("MIDI channels: 1–6 (one per voice)")
print("─" * 50)

# ── Main Loop ─────────────────────────────────────────────────────────────────

while True:
    now = time.monotonic()

    # ─── Button 7: Shift / Scale cycle ───────────────────────────────────────
    b7 = buttons[7].value
    if b7 != last_btn[7]:
        last_btn[7] = b7
        if not b7:
            shift_on   = True
            shift_used = False
            pixel[0]   = (180, 180, 180)
        else:
            shift_on = False
            if not shift_used:
                cfg["scale"] = (cfg["scale"] + 1) % len(SCALES)
                sc = SCALES[cfg["scale"]]
                print(f"Scale → {sc['name']}")
                flash(sc["letter"])
                rekey_active_voices()

    # ─── Button 8: Breathe / Panic / Outro ───────────────────────────────────
    b8 = buttons[8].value
    if b8 != last_btn[8]:
        last_btn[8] = b8
        if not b8:
            btn8_t = now
        else:
            if btn8_t is not None:
                held   = now - btn8_t
                btn8_t = None

                if shift_on:
                    shift_used = True
                    for _i in range(6):
                        if voices[_i]["state"] in (WAKING, ACTIVE):
                            begin_release(_i, now, OUTRO_DUR)
                    flash("breathe", 1.0)
                    print("Outro — slow release initiated")

                elif held > 0.5:
                    midi_panic()
                    for _i in range(6):
                        voices[_i] = new_voice(_i)
                    breathe_on = False
                    flash("panic", 0.8)
                    print("Panic reset")

                else:
                    if breathe_on and not breathe_rev:
                        breathe_rev   = True
                        breathe_rev_t = now
                        print("Breathe ← reversed")
                    else:
                        breathe_on  = True
                        breathe_t0  = now
                        breathe_rev = False
                        breathe_cc7 = [100] * 6
                        flash("breathe", 0.5)
                        print("Breathe →")

    # ─── Buttons 1–6: Voice toggles & shift combos ───────────────────────────
    for i in range(1, 7):
        bv = buttons[i].value
        if bv != last_btn[i]:
            last_btn[i] = bv
            if not bv:
                if shift_on:
                    shift_used = True

                    if i == 1:
                        cfg["root"] = max(0, cfg["root"] - 12)
                        flash("oct_down")
                        rekey_active_voices()
                        print(f"Root → {cfg['root']}")

                    elif i == 2:
                        cfg["root"] = min(108, cfg["root"] + 12)
                        flash("oct_up")
                        rekey_active_voices()
                        print(f"Root → {cfg['root']}")

                    elif i == 3:
                        cfg["gravity"] = (cfg["gravity"] + 1) % len(GRAVITY_NAMES)
                        flash(GRAV_ICONS[cfg["gravity"]])
                        print(f"Gravity → {GRAVITY_NAMES[cfg['gravity']]}")

                    elif i == 4:
                        cfg["drift_speed"] = (cfg["drift_speed"] + 1) % len(DRIFT_SPEEDS)
                        flash(DRIFT_ICONS[cfg["drift_speed"]])
                        print(f"Drift speed → {DRIFT_SPEED_NAMES[cfg['drift_speed']]}")

                    elif i == 5:
                        do_scatter(now)

                    elif i == 6:
                        for _j in range(6):
                            begin_release(_j, now)
                        flash("release_all")
                        print("Release all voices")

                else:
                    toggle(i - 1, now)
                    update_onboard_led()

    # ─── Periodic updates ────────────────────────────────────────────────────
    update_voices(now)
    update_breathe(now)
    update_pixel(now)
    update_onboard_led()
    render_matrix(now)

    time.sleep(0.05)