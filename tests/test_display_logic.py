"""The van display's logic, away from the screen: page order, sliders, the
heater dial and fan, G-force, tanks and levelling.

The drawing is checked by tools/preview_display.py (every page rendered, no
text out of its box); this checks the sums and decisions behind it, with the
display's real code (display/ui.py) and the same stand-in framebuffer.

Run:  python tests/test_display_logic.py
"""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "tools"))
import preview_display as pv                                     # noqa: E402

pv.install_framebuf()
sys.path.insert(0, os.path.join(REPO, "display"))
import gfx                                                       # noqa: E402
import ui                                                        # noqa: E402

passed = []


def ok(name):
    passed.append(name)
    print("ok  ", name)


def make(pages=None):
    buf = bytearray(480 * 320 * 2)
    fb = pv.FrameBuffer(buf, 480, 320, pv.FrameBuffer.RGB565)
    return ui.UI(fb, gfx.Fonts(os.path.join(REPO, "display", "fonts")), pages=pages,
                 now=lambda: 1790330117, logo=os.path.join(REPO, "display", "logo.bin"),
                 mono=lambda: 1000, buf=buf)


# ---- pages and the swipe ring --------------------------------------------------------
u = make()
assert u.pages == ["home", "power", "heater", "level", "switches", "tanks", "drive"]
old = make(["home", "power", "heater", "level", "switches", "drive"])     # a display's own config
assert old.pages[-2:] == ["tanks", "drive"]
assert make(["home", "level"]).pages == ["home", "level", "tanks"]
ok("Tanks sits before Drive, even where a display's config lists its pages without it")


def where(v):
    return "logo" if v.logo_page else v.pages[v.page]


u.page = 0
right = [where(u)]
for _ in range(10):
    u.swipe(1)
    right.append(where(u))
assert right == ["home", "power", "heater", "level", "switches", "tanks", "drive", "logo",
                 "home", "power", "heater"], right
left = [where(u)]
for _ in range(10):
    u.swipe(-1)
    left.append(where(u))
assert left == ["heater", "power", "home", "logo", "drive", "tanks", "switches", "level",
                "heater", "power", "home"], left
ok("swiping goes round: logo, Home ... Tanks, Drive, and back to the logo (games: three taps on it)")

u.confirm = ("off", ["Turn the heater OFF?"])
p = u.page
u.swipe(1)
assert u.page == p
u.confirm = None
ok("no swiping away from a question on screen")

# ---- sliders ----------------------------------------------------------------------------
u.bright_open = True
u.render()
x0, w = u._slides["bright"]
assert u.slide(("slide", "bright"), x0 - 50, 0) == ("brightness", 0.1)
assert u.slide(("slide", "bright"), x0 + w + 50, 0) == ("brightness", 1.0)
assert u.slide(("slide", "bright"), x0 + w + 50, 0) is None            # unchanged: nothing to send
vals = []
for px in list(range(x0, x0 + w, 10)) + [x0 + w]:          # always the very end too
    u.slide(("slide", "bright"), px, 0)
    vals.append(u.brightness)
assert vals == sorted(vals) and vals[0] == 0.1 and vals[-1] == 1.0
u.bright_open = False
ok("screen brightness slider: 10% to 100%, rises along the bar, held at the ends")

u.page, u.sub = u.pages.index("power"), "lights"
u.render()
x0, w = u._slides["amb"]
steps = set()
for px in range(x0 - 20, x0 + w + 20, 3):
    u.slide(("slide", "amb"), px, 0)
    steps.add(u.ambient["bright"])
assert min(steps) == 10 and max(steps) == 100 and all(s % 5 == 0 for s in steps)
ok("ambient light slider: 10% to 100% in steps of 5")

cx, cy, R, th = u.DIAL
rm = R - th / 2
temps = []
for deg in range(-135, 136, 5):
    a = math.radians(deg)
    u.slide(("dial", 0), cx + math.sin(a) * rm, cy - math.cos(a) * rm)
    temps.append(u._target())
assert temps[0] == u.T_MIN and temps[-1] == u.T_MAX and temps == sorted(temps)
before = u._target()
assert u.slide(("dial", 0), cx, cy) is None and u._target() == before    # the middle does nothing
a = math.radians(178)                                                    # deep in the gap at the bottom
u.slide(("dial", 0), cx + math.sin(a) * rm, cy - math.cos(a) * rm)
assert u._target() == before
ok("heater dial: 5 to 35 C round the ring; the middle and the gap at the bottom do nothing")

# ---- heater fan -----------------------------------------------------------------------------
u.data = {"heater": {"connected": True, "on": False, "mode_code": 10, "fan_level": 3}}
assert u.heater_body("air")["level"] == 3                    # the heater's own, until chosen
u._do(("fanlvl", 1))
body = u.heater_body("vent")
assert body["level"] == 1 and body["action"] == "vent"
u.confirm = None
u._do(("heat", "vent"))
assert u.confirm[0] == "vent" and "fan only, speed 1" in u.confirm[1][0].lower()
u.confirm = None
u.data["heater"].update(on=True, mode_code=6)
assert u._mode(u._heater()) == "vent"
ok("heater fan: the chosen speed goes with the next command; Fan only asks first")

# ---- G-force ---------------------------------------------------------------------------------
g = make()
g.gforce_sample({"ok": True, "roll": 0, "pitch": 0, "axes": {"x": 0, "y": 1.02, "z": 0}})
assert g.g_now == (0.0, 0.0, 0.0)
g.gforce_sample({"ok": True, "roll": 10, "pitch": -15, "axes": {"x": 0, "y": 1.02, "z": 0}})
lat, lon, _ = g.g_now
assert abs(lat - math.tan(math.radians(10))) < 1e-9 and abs(lon - math.tan(math.radians(-15))) < 1e-9
assert g.g_peak["right"] > 0.17 and g.g_peak["brk"] > 0.26 and g.g_peak["acc"] == 0
g.gforce_sample({"ok": True, "roll": 0, "pitch": 0, "axes": {"x": 0, "y": 1.30, "z": 0}})
assert g.g_now[2] > 0.25 and g.g_peak["bump"] > 0.25                    # a jolt over the steady 1 g
g.gforce_sample({"ok": False})                                           # a failed reading changes nothing
for _ in range(40):
    g.gforce_sample({"ok": True, "roll": 1, "pitch": 1, "axes": {"x": 0, "y": 1.02, "z": 0}})
assert len(g.g_trail) == 24
g._g_do("reset")
assert all(v == 0 for v in g.g_peak.values())
ok("G-force: g = tan(angle); right and braking read the right way; bumps; peaks reset; trail kept short")

# ---- tanks ---------------------------------------------------------------------------------------
st = ui.UI._tank_state
assert [st("fresh", p)[0] for p in (90, 35, 34, 15, 14)] == ["Plenty", "Plenty", "Getting low",
                                                              "Getting low", "Refill now"]
assert [st("grey", p)[0] for p in (33, 65, 66, 85, 86)] == ["Fine", "Fine", "Empty soon",
                                                           "Empty soon", "Empty now"]
t = make()
assert t._tank_levels()["demo"]
t.data = {"tanks": {"fresh": 40, "grey": 70}}
assert t._tank_levels() == {"fresh": 40, "grey": 70}
ok("tanks: fresh warns as it empties, grey as it fills; the hub's levels replace the samples")

# ---- levelling ------------------------------------------------------------------------------------
vt = ui.van_tilt
assert vt(0.9, 1.0) == 0 and vt(-1.0, 1.0) == 0                     # within the tolerance: level
assert vt(1.5, 1.0) == 1.5 and vt(-2.0, 1.0) == -3.0                # only the excess, x3
assert vt(1.0001, 1.0) < 0.01                                       # no jump at the edge
assert vt(90, 1.0) == ui.VAN_MAX_DEG
ok("Level drawing: level within the tolerance, the excess x3 beyond it, no jump, capped")

lv = make()
assert lv.wheel_raise(0, 0) == [0, 0, 0, 0]
cm = lv.wheel_raise(1.0, 0)                 # right side high: raise the left wheels
assert min(cm) == 0 and cm[0] == cm[2] > 0 and cm[1] == cm[3] == 0
assert abs(cm[0] - lv.van[1] * math.tan(math.radians(1)) / 10) < 1e-9
cm = lv.wheel_raise(0, -1.0)                # nose down: raise the front wheels
assert cm[0] == cm[1] > 0 and cm[2] == cm[3] == 0
ok("wheel heights: the highest wheel stays, the others come up to it by track x tan(angle)")

# the display's factory reset: what it clears, read from display/main.py itself
import ast                                                       # noqa: E402
_src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "display", "main.py"), encoding="utf-8").read()
_consts = {}
for _n in ast.parse(_src).body:
    if isinstance(_n, ast.Assign) and isinstance(_n.targets[0], ast.Name):
        try:
            _consts[_n.targets[0].id] = ast.literal_eval(_n.value)
        except ValueError:
            pass
_wipe = [_consts.get(x.id, x.id) if isinstance(x, ast.Name) else ast.literal_eval(x)
         for x in next(n.value for n in ast.parse(_src).body if isinstance(n, ast.Assign)
                       and getattr(n.targets[0], "id", "") == "RESET_FILES").elts]
assert set(_wipe) >= {"hub_key.txt", "wifi_learned.json", "wifi_hubnet.json", "display_prefs.json"}, _wipe
assert not [f for f in _wipe if f.startswith("ota") or f == "touch_cal.json" or f.endswith(".py")], _wipe
assert _consts["RESET_HOLD_S"] == 10
ok("display factory reset: pairing, learned networks and preferences cleared; updates and touch kept")

print("\nALL OK - %d checks" % len(passed))
