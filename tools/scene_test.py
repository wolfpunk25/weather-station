"""Off-board harness: stub CircuitPython, exec everything before the main
loop, then drive the module directly."""
import sys, types, math, time as _time

# ---- stubs ----------------------------------------------------------------
class Pin:
    def __init__(s, n): s.n = n
    def __repr__(s): return s.n

board = types.ModuleType("board")
for _n in ("GP0","GP1","GP4","GP16","GP17","GP18","GP19","GP20","GP21",
           "GP22","GP23","GP25","GP26","LED"):
    setattr(board, _n, Pin(_n))

busio = types.ModuleType("busio")
class I2C:
    def __init__(s, **k): pass
class UART:
    def __init__(s, **k): s.buf = bytearray()
    def write(s, b): s.buf.extend(b)
busio.I2C, busio.UART = I2C, UART

digitalio = types.ModuleType("digitalio")
class Direction: INPUT="in"; OUTPUT="out"
class Pull: UP="up"
class DigitalInOut:
    def __init__(s, p): s.pin=p; s.value=True; s.direction=None; s.pull=None
digitalio.Direction, digitalio.Pull, digitalio.DigitalInOut = Direction, Pull, DigitalInOut

usb_midi = types.ModuleType("usb_midi")
class Port:
    def __init__(s): s.msgs=[]
    def write(s,b): s.msgs.append(bytes(b))
usb_midi.ports = [Port(), Port()]

neopixel = types.ModuleType("neopixel")
class NeoPixel:
    def __init__(s,p,n,**k): s.v=[(0,0,0)]*n
    def __setitem__(s,i,v): s.v[i]=v
    def __getitem__(s,i): return s.v[i]
    def show(s): pass
neopixel.NeoPixel = NeoPixel

pwmio = types.ModuleType("pwmio")
class PWMOut:
    def __init__(s,p,**k): s.duty_cycle=0
pwmio.PWMOut = PWMOut

ht = types.ModuleType("adafruit_ht16k33")
mat = types.ModuleType("adafruit_ht16k33.matrix")
class Matrix8x8:
    def __init__(s,i2c,address=0x70):
        s.g=[[0]*8 for _ in range(8)]; s.auto_write=True; s.brightness=1.0
    def fill(s,v): s.g=[[v]*8 for _ in range(8)]
    def __setitem__(s,xy,v): s.g[xy[1]][xy[0]]=v
    def show(s): pass
mat.Matrix8x8 = Matrix8x8
ht.matrix = mat

for name, mod in (("board",board),("busio",busio),("digitalio",digitalio),
                  ("usb_midi",usb_midi),("neopixel",neopixel),("pwmio",pwmio),
                  ("adafruit_ht16k33",ht),("adafruit_ht16k33.matrix",mat)):
    sys.modules[name] = mod

# make the boot animation instant
_real_sleep = _time.sleep
_t = [0.0]
class FakeTime:
    @staticmethod
    def monotonic(): return _t[0]
    @staticmethod
    def sleep(s): _t[0] += s
sys.modules["time"] = FakeTime

# ---- load module ----------------------------------------------------------
src = open(sys.argv[1]).read()
head = src.split("\nwhile True:")[0]
ns = {"__name__": "ws"}
exec(compile(head, "code.py", "exec"), ns)

def ascii_frame(m):
    # undo ROTATE so the dump reads the way the artwork was drawn
    rot = ns["ROTATE"]
    out = []
    for y in range(8):
        row = ""
        for x in range(8):
            if rot == 0: px = m.g[y][x]
            elif rot == 90: px = m.g[7-x][y]
            elif rot == 180: px = m.g[7-y][7-x]
            else: px = m.g[x][7-y]
            row += "#" if px else "."
        out.append(row)
    return out

def show(label):
    print("  " + label)
    for r in ascii_frame(ns["mx"]):
        print("    " + r)

def press(k):
    ns["buttons"][k].value = False
def rel(k):
    ns["buttons"][k].value = True
def tick(n=1, dt=0.045):
    for _ in range(n):
        _t[0] += dt
        now = _t[0]
        ns["scan_inputs"](now); ns["update_pending"](now); ns["update_held"](now)
        ns["update_wind"](now); ns["update_timbre"]()
        ns["update_particles"](now); ns["render_matrix"](now); ns["update_pixel"](now)
        ns["update_blue_led"](now)

SUN, WIND, RAIN = ns["SUN"], ns["WIND"], ns["RAIN"]
port = usb_midi.ports[1]

def midi_since(i):
    return port.msgs[i:]

def summarise(msgs):
    from collections import Counter
    c = Counter()
    for m in msgs:
        st, ch = m[0] & 0xF0, m[0] & 0x0F
        kind = {0x90:"on",0x80:"off",0xB0:"cc",0xE0:"bend"}.get(st, hex(st))
        c[(kind, ch+1)] += 1
    return dict(sorted(c.items()))

print("=" * 62)
print("SCENES")
print("=" * 62)
combos = [((),"Clear"), ((SUN,),"Sunshine"), ((WIND,),"Breeze"), ((RAIN,),"Rain"),
          ((SUN,WIND),"Heat Haze"), ((SUN,RAIN),"Rainbow"),
          ((WIND,RAIN),"Squall"), ((SUN,WIND,RAIN),"Storm")]
for mods, name in combos:
    ns["do_panic"]()
    for m in mods: press(m)
    press(6)
    tick(30)
    i = len(port.msgs)
    tick(40)
    print()
    print("%-10s  weather=%s  pixel=%s" % (name, ns["weather_name"](), ns["pixel"][0]))
    show("matrix")
    print("    midi:", summarise(midi_since(i)))
    rel(6)
    for m in mods: rel(m)
    tick(5)

print()
print("=" * 62)
print("NOTE ROUTING (channels used on a fresh key press)")
print("=" * 62)
for mods, name in combos:
    ns["do_panic"]()
    tick(3)
    for m in mods: press(m)
    tick(3)
    i = len(port.msgs)
    press(6); tick(12)
    notes = [(m[0] & 0x0F) + 1 for m in midi_since(i) if (m[0] & 0xF0) == 0x90]
    print("%-10s note-on channels: %s" % (name, sorted(set(notes))))
    rel(6)
    for m in mods: rel(m)
    tick(4)

print()
print("=" * 62)
print("TAPS, GATING AND PANIC")
print("=" * 62)
ns["do_panic"](); tick(3)

def tap(k, hold=0.15):
    press(k); tick(int(hold/0.045)+1); rel(k); tick(2)

s0 = dict(ns["cfg"])
tap(SUN); tap(WIND); tap(RAIN)
print("after one tap each:", ns["cfg"], " (was", s0, ")")

# a tap that plays a note must NOT change a setting
before = dict(ns["cfg"])
press(SUN); tick(3); press(7); tick(3); rel(7); tick(2); rel(SUN); tick(2)
print("tap+note changed settings?", ns["cfg"] != before, "(expected False)")

# a tap while another modifier is down must NOT change a setting
before = dict(ns["cfg"])
press(RAIN); tick(2); press(WIND); tick(3); rel(WIND); tick(2); rel(RAIN); tick(3)
print("non-solo tap changed settings?", ns["cfg"] != before, "(expected False)")

# stuck-note check: play everything under every modifier, then let go
ns["do_panic"](); tick(3)
for m in (SUN, WIND, RAIN): press(m)
for k in (4,5,6,7,8): press(k)
tick(60)
for k in (4,5,6,7,8): rel(k)
for m in (SUN, WIND, RAIN): rel(m)
tick(40)
sounding = {}
for msg in port.msgs:
    st, ch, n = msg[0] & 0xF0, msg[0] & 0x0F, msg[1]
    if st == 0x90: sounding[(ch, n)] = sounding.get((ch, n), 0) + 1
    elif st == 0x80: sounding[(ch, n)] = sounding.get((ch, n), 0) - 1
    elif st == 0xB0 and n in (120, 123): sounding = {}
stuck = {k: v for k, v in sounding.items() if v > 0}
print("stuck notes after full release:", stuck if stuck else "none")

# panic gesture
press(4); tick(3); rel(4); tick(2)
for m in (SUN, WIND, RAIN): press(m)
tick(40)
print("panic fired:", not ns["panic_armed"], "(expected True)")
for m in (SUN, WIND, RAIN): rel(m)
tick(3)
print("held notes after panic:", ns["held"])

print()
print("=" * 62)
print("SCALE / OCTAVE RANGE")
print("=" * 62)
for si in range(len(ns["SCALES"])):
    ns["cfg"]["scale"] = si
    for oi in range(len(ns["OCTAVE_ROOTS"])):
        ns["cfg"]["octave"] = oi
        lo = min(ns["scale_note"](d, -1) for d in range(-6, 12))
        hi = max(ns["scale_note"](d,  1) for d in range(-6, 12))
        assert 0 <= lo <= 127 and 0 <= hi <= 127
    print("%-9s ok  (oct %s: %s)" % (
        ns["SCALES"][si][0], ns["OCTAVE_NAMES"][oi],
        [ns["scale_note"](d) for d in range(5)]))

print()
print("=" * 62)
print("DIN FOLD-DOWN (DIN_CHANNEL = %s)" % ns["DIN_CHANNEL"])
print("=" * 62)

uart = ns["uart"]

def din_msgs(start):
    b = bytes(uart.buf[start:])
    return [b[i:i+3] for i in range(0, len(b) - 2, 3)]

for mods, name in [((), "Clear"), ((SUN,), "SUN"), ((WIND,), "WIND"),
                   ((RAIN,), "RAIN"), ((SUN, WIND, RAIN), "all three")]:
    ns["do_panic"](); tick(4)
    for m in mods: press(m)
    tick(4)
    i = len(uart.buf)
    press(6); tick(45); rel(6); tick(8)
    for m in mods: rel(m)
    tick(6)
    ons = [m for m in din_msgs(i) if m[0] & 0xF0 == 0x90]
    chans = sorted(set((m[0] & 0x0F) + 1 for m in din_msgs(i)))
    print("%-10s DIN note-ons: %-4d  distinct pitches: %-4d  channels used: %s"
          % (name, len(ons), len(set(m[1] for m in ons)), chans))

# The hard one: overlapping layers must not leave a note hanging on the
# folded channel, and the hold-count must return to empty.
ns["do_panic"](); tick(4)
for m in (SUN, WIND, RAIN): press(m)
for k in (4, 5, 6, 7, 8): press(k)
tick(90)
for k in (4, 5, 6, 7, 8): rel(k)
for m in (SUN, WIND, RAIN): rel(m)
tick(60)

i = 0
sounding = {}
for m in din_msgs(0):
    st, n = m[0] & 0xF0, m[1]
    if st == 0x90: sounding[n] = sounding.get(n, 0) + 1
    elif st == 0x80: sounding[n] = sounding.get(n, 0) - 1
    elif st == 0xB0 and n in (120, 123): sounding = {}
stuck = {k: v for k, v in sounding.items() if v > 0}
print()
print("DIN stuck notes after a full storm release:", stuck if stuck else "none")
print("internal hold-count left over:", ns["_din_held"] if ns["_din_held"] else "empty")

usb_bytes = sum(len(m) for m in port.msgs)
din_bytes = len(uart.buf)
print("USB bytes %d -> DIN bytes %d  (%.0f%% of USB traffic)"
      % (usb_bytes, din_bytes, 100.0 * din_bytes / usb_bytes))
