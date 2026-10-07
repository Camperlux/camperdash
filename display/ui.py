# The screens. Pure drawing and hit-testing: no network, no hardware, no
# asyncio - main.py feeds data in and carries out what a tap asks for. That
# keeps this file runnable on the PC, where tools/preview_display.py renders
# every page to PNG from sample data.
#
# Landscape, 480 x 320:
#     header    y   0 -  34   page title, alerts, clock, link to the hub
#     body      y  36 - 276
#     tabs      y 278 - 320

import math
import struct
from array import array
import framebuf
import gfx
import icons
from aa import AA
from gfx import (BG_TOP, BG_BOT, CARD_HI, EMBER)
from gfx import (BG, PANEL, PANEL2, LINE, TXT, MUTED, GREEN, AMBER, RED, BLUE,
                 CYAN, BRAND, FLAME, rrect, bar, dot)

W, H = 480, 320
HEAD_H = 34
TAB_Y = 278
BODY_Y = 36

PAGE_TITLES = {"home": "Home", "tanks": "Tanks", "drive": "Drive", "lights": "Lights", "power": "Power", "heater": "Heater", "level": "Level",
               "battery": "Battery", "switches": "Switches"}
# Battery and Lights live under Power now, as its sub-pages
POWER_SUBS = (("Flow", None), ("Battery", "battery"), ("Lights", "lights"))

WATER = {1: "40°C", 2: "60°C", 3: "Boost"}
ACTION_LABEL = {"off": "Off", "air": "Air heat", "water": "Water heat",
                "combi": "Water + air", "read": "Refresh", "vent": "Fan only"}
DASH = "–"

DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")

# Open-Meteo WMO codes, worded as on the web Weather page
WMO = {0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
       45: "Fog", 48: "Rime fog", 51: "Light drizzle", 53: "Drizzle",
       55: "Heavy drizzle", 56: "Freezing drizzle", 57: "Freezing drizzle",
       61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain",
       67: "Freezing rain", 71: "Light snow", 73: "Snow", 75: "Heavy snow",
       77: "Snow grains", 80: "Showers", 81: "Showers", 82: "Violent showers",
       85: "Snow showers", 86: "Snow showers", 95: "Thunderstorm",
       96: "Thunder + hail", 99: "Thunder + hail"}

SUN = gfx.rgb(245, 190, 60)
MOON = gfx.rgb(225, 220, 205)
CLOUD = gfx.rgb(196, 192, 204)
STORM = gfx.rgb(118, 112, 128)
VAN_BODY = gfx.rgb(196, 190, 200)
VAN_GLASS = gfx.rgb(70, 110, 150)
VAN_TRIM = gfx.rgb(120, 114, 126)
VAN_WHEEL = gfx.rgb(84, 80, 92)      # tyres: lighter than a real tyre, or the dark card hides them
VAN_HUB = gfx.rgb(150, 144, 156)

# The web Level page's van drawings (static/level.html), in its 300 x 200 box,
# turning about (150, 140) - the middle of the ground line. Shapes are
# ("p", points), ("r", x, y, w, h) or ("c", cx, cy, r); rounded corners are cut
# square, which at this size nobody can see. Colours are given by name and
# looked up when drawn, so the night palette reaches them.
VAN_FRONT = (
    ("VAN_BODY", ("p", (78, 140, 78, 64, 86, 53, 214, 53, 222, 64, 222, 140))),
    ("VAN_GLASS", ("r", 90, 62, 120, 30)),
    ("VAN_TRIM", ("r", 66, 74, 12, 9)), ("VAN_TRIM", ("r", 222, 74, 12, 9)),
    ("VAN_TRIM", ("r", 86, 100, 22, 11)), ("VAN_TRIM", ("r", 192, 100, 22, 11)),
    ("VAN_TRIM", ("r", 114, 102, 72, 8)),
    ("VAN_WHEEL", ("r", 72, 126, 24, 30)), ("VAN_WHEEL", ("r", 204, 126, 24, 30)),
)
VAN_SIDE = (
    # the body in two parts: the smooth-shape drawing takes convex shapes only,
    # and the outline dents in where the windscreen meets the bonnet
    ("VAN_BODY", ("p", (40, 140, 40, 60, 46, 52, 54, 50, 198, 50, 232, 86, 232, 140))),
    ("VAN_BODY", ("p", (231, 85, 262, 96, 267, 102, 268, 110, 268, 140, 231, 140))),
    ("VAN_GLASS", ("p", (188, 58, 204, 58, 228, 84, 212, 84))),
    ("VAN_GLASS", ("r", 58, 64, 58, 26)), ("VAN_GLASS", ("r", 124, 64, 56, 26)),
    ("VAN_WHEEL", ("c", 86, 142, 16)), ("VAN_WHEEL", ("c", 224, 142, 16)),
    ("VAN_HUB", ("c", 86, 142, 7)), ("VAN_HUB", ("c", 224, 142, 7)),
)
VAN_GAIN, VAN_MAX_DEG = 3, 35      # as the web page: tilt shown x3, up to 35 degrees


def van_tilt(deg, ok_deg):
    """The angle to draw a van at for a real tilt of deg. Within the "level"
    tolerance (the hub's level_ok, 1 degree unless set) it is drawn level, as
    a van that close needs nothing doing; beyond it, only the part over the
    tolerance is drawn, magnified so a real problem still shows plainly. So a
    1 degree tilt no longer looks like 3, and the drawing never jumps at the
    edge of the tolerance."""
    a = abs(deg) - ok_deg
    if a <= 0:
        return 0.0
    a = min(VAN_MAX_DEG, a * VAN_GAIN)
    return a if deg > 0 else -a

# Default tick-off list for the Drive page; config.CHECKLIST replaces it.
CHECKLIST = ["Roof vents shut", "Gas off", "Cupboards latched", "Windows shut",
             "Step and awning in", "Ramps removed"]


def sun_times(t, lat, lon):
    """Sunrise and sunset for the UTC day containing unix time t, as unix
    times, or None when the sun does not rise or set that day. The standard
    almanac method (zenith 90.833 degrees), good to a minute or two - plenty
    for choosing a colour scheme - and it needs no internet."""
    y, m, d = _civil(t)[:3]
    day0 = _days_from_civil(y, m, d)
    n = day0 - _days_from_civil(y, 1, 1) + 1
    lng_h = lon / 15.0
    rad, deg = math.radians, math.degrees
    out = []
    for rising in (True, False):
        tt = n + ((6 if rising else 18) - lng_h) / 24.0
        M = 0.9856 * tt - 3.289
        L = (M + 1.916 * math.sin(rad(M)) + 0.020 * math.sin(rad(2 * M)) + 282.634) % 360
        RA = deg(math.atan(0.91764 * math.tan(rad(L)))) % 360
        RA = (RA + (L // 90) * 90 - (RA // 90) * 90) / 15.0
        sin_dec = 0.39782 * math.sin(rad(L))
        cos_dec = math.cos(math.asin(sin_dec))
        cos_h = ((math.cos(rad(90.833)) - sin_dec * math.sin(rad(lat)))
                 / (cos_dec * math.cos(rad(lat))))
        if cos_h > 1 or cos_h < -1:
            return None
        H = (360 - deg(math.acos(cos_h))) if rising else deg(math.acos(cos_h))
        T = H / 15.0 + RA - 0.06571 * tt - 6.622
        out.append(day0 * 86400 + int(((T - lng_h) % 24) * 3600))
    return out[0], out[1]


# Night palette: red on black, dim, so the screen does not wreck night vision
# or light up the van. Everything that is a colour name in this module and in
# gfx is swapped, so every page follows without knowing about it.
_NIGHT = {
    "BG": (0, 0, 0), "PANEL": (16, 3, 3), "PANEL2": (28, 5, 5), "LINE": (52, 9, 9),
    "TXT": (205, 38, 28), "MUTED": (118, 22, 16), "GREEN": (150, 70, 18),
    "AMBER": (215, 95, 18), "RED": (255, 30, 18), "BLUE": (130, 30, 50),
    "CYAN": (140, 40, 30), "BRAND": (190, 70, 20), "FLAME": (225, 80, 20),
    "SUN": (200, 80, 20), "MOON": (160, 40, 25), "CLOUD": (120, 30, 22),
    "STORM": (80, 18, 14), "VAN_BODY": (105, 24, 18), "VAN_GLASS": (45, 8, 8),
    "BG_TOP": (6, 0, 0), "BG_BOT": (0, 0, 0), "CARD_HI": (30, 6, 6), "EMBER": (235, 60, 20),
    "VAN_TRIM": (70, 14, 10), "VAN_WHEEL": (60, 12, 10), "VAN_HUB": (110, 22, 16),
}
_DAY = {}


def set_night(on):
    """Swap every named colour, here and in gfx, for its night version (or
    back). Pages read the names at draw time, so nothing else has to change."""
    g = globals()
    if not _DAY:
        for name in _NIGHT:
            _DAY[name] = g[name]
    for name, v in _NIGHT.items():
        c = gfx.rgb(*v) if on else _DAY[name]
        g[name] = c
        if hasattr(gfx, name):
            setattr(gfx, name, c)


# ---- small helpers -----------------------------------------------------------

def hsv(h, s, v=1.0):
    """Hue 0-360, saturation and value 0-1, to an (r, g, b) of 0-255."""
    h = (h % 360) / 60.0
    i = int(h)
    f = h - i
    p, q, t = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
    r, g, b = ((v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q))[i % 6]
    return int(r * 255), int(g * 255), int(b * 255)


def _num(v, fmt="%.1f"):
    return DASH if v is None else fmt % v


def _hm(hours):
    """A duration for people: '2 h 10 m', '45 m', '99+ h'."""
    if hours is None or hours < 0:
        return DASH
    if hours >= 99:
        return "99+ h"
    m = int(round(hours * 60))
    if m < 60:
        return "%d m" % m
    return "%d h %02d m" % (m // 60, m % 60)


def _hm_short(hours):
    """The same, tight: '12h15m', '45m', '99h+' - for when '12 h 15 m' will
    not fit (a slow charge makes a long wait, and a long line)."""
    if hours is None or hours < 0:
        return DASH
    if hours >= 99:
        return "99h+"
    m = int(round(hours * 60))
    return "%dm" % m if m < 60 else "%dh%02dm" % (m // 60, m % 60)


def _fits(font, width, options):
    """The first of options that fits width in font; the last one if none do."""
    for t in options:
        if font.width(t) <= width:
            return t
    return options[-1]


def _eta_options(hours, charging):
    """How long until full or empty, longest wording first, then a plain
    word for when even the tightest will not fit."""
    if charging:
        return ("Full in " + _hm(hours), "Full in " + _hm_short(hours), "Full " + _hm_short(hours), "Charging")
    return (_hm(hours) + " left", _hm_short(hours) + " left", _hm_short(hours), "Discharging")


def _days_from_civil(y, m, d):
    y -= m <= 2
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def _civil(t):
    """Unix seconds to (year, month, day, hour, minute). Done by hand rather
    than with time.gmtime, whose epoch differs between MicroPython ports."""
    days, secs = divmod(int(t), 86400)
    z = days + 719468
    era = (z if z >= 0 else z - 146096) // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + 3 if mp < 10 else mp - 9
    # weekday: 1 Jan 1970 was a Thursday; 0 = Monday
    return (yoe + era * 400 + (m <= 2), m, d, secs // 3600, (secs // 60) % 60,
            secs % 60, (days + 3) % 7)


def local_parts(t, offset_min=0, dst_eu=True):
    """Unix time to local (year, month, day, hour, minute, second, weekday),
    with the EU summer-time rule: from 01:00 UTC on the last Sunday of March to
    the last Sunday of October."""
    if dst_eu:
        y = _civil(t)[0]
        start = _days_from_civil(y, 3, 31)
        start -= (start + 4) % 7                # back to the last Sunday
        end = _days_from_civil(y, 10, 31)
        end -= (end + 4) % 7
        if start * 86400 + 3600 <= t < end * 86400 + 3600:
            offset_min += 60
    return _civil(t + offset_min * 60)


def local_time(t, offset_min=0, dst_eu=True):
    """Unix time to local (hour, minute)."""
    p = local_parts(t, offset_min, dst_eu)
    return p[3], p[4]


def radius_for(deg, r_max, max_deg):
    """The web level page's non-linear vial scale, so both agree."""
    a = min(abs(deg), max_deg)
    return r_max * math.sqrt(a / max_deg) if max_deg > 0 else 0


# ---- the UI ------------------------------------------------------------------

class UI:
    def __init__(self, fb, fonts, pages=None, now=None, tz=(0, True), logo="logo.bin",
                 mono=None, buf=None):
        self.fb = fb
        # smooth shapes straight into the frame buffer; gfx's helpers use it too
        self.aa = AA(buf, W, H, fb) if buf is not None else None
        self._buf = buf
        self._fstatic = None        # the flow page without its figures, kept
        self._fstatic_key = None
        gfx.AA = self.aa
        self._bg_rows = None        # the background gradient, one colour per band
        self._icons = {}            # icon images, by (name, colour, size, background)
        self.f = fonts
        self.pages = [p for p in (pages or ["home", "power", "heater", "level", "switches",
                                            "tanks", "drive"])
                      if p in PAGE_TITLES]
        if "tanks" not in self.pages:
            at = self.pages.index("drive") if "drive" in self.pages else len(self.pages)
            self.pages.insert(at, "tanks")
        # The water tanks, as percentages. A mock-up until sensors are fitted:
        # the hub's "tanks" in /api/data replace these once it reports them.
        self.tanks = {"fresh": 90, "grey": 33, "demo": True}
        # The Level page's G-force view, for driving: the latest reading,
        # a short trail for the g-ball, and peaks. Its zero is the levelling
        # calibration - one calibration for both.
        self.g_now = None           # (lateral, longitudinal, vertical) in g
        self.g_trail = []
        self.g_peak = {"acc": 0.0, "brk": 0.0, "left": 0.0, "right": 0.0, "bump": 0.0}
        self.g_base = None          # the sensor's own idea of 1 g, learnt slowly
        self.page = 0
        self.data = None            # last /api/data
        self.level = None           # last /api/level "level" block, fresher
        self.link = "wifi"          # wifi | down | stale | ok
        self.link_note = "Joining WiFi"
        self.now = now              # function returning unix time, or None
        self.tz = tz                # (UTC offset in minutes, EU summer time)
        self.weather = None         # last /api/weather: {"meta", "data"}
        self.seconds = True         # draw the second hand (off while dimmed)
        self.brightness = 1.0       # 0.1 - 1.0, adjusted from the screen
        self.bright_open = False
        self.sound_on = True
        self.hit = None             # what the last tap landed on, if anything
        self._vanfit = {}           # van drawing scale and position, per box
        self.alerts = None          # from /api/alerts, asked every 2 s; else /api/data's
        self.alert_sound = True     # the hub's own setting for alert sound
        self.saver = False          # the screensaver: just the logo
        self.logo_page = False      # the logo as a page: swipe past either end to it
        self._slides = {}           # each slider's track (x, width), as last drawn
        self.dim_on = True          # dim when idle, by day (at night it goes off instead)
        self.dbatt = None           # the display's own battery: {"v", "pct", "state"}
        self.offmode = False        # dark whenever idle, until morning
        self._logo_path = logo
        self._logo = None           # loaded on first use; False if missing
        self.level_view = "bubble"  # or "wheels": centimetres under each wheel
        self.van = (3640, 1777)     # wheelbase and track, mm (config VAN_*)
        self.checklist = list(CHECKLIST)
        self.ticks = set()          # ticked items on the Drive page, by their text
        self.night = False          # the red night palette is showing
        self.night_mode = "auto"    # auto | on | off, set in the Display panel
        self.mono = mono            # seconds, for the kitchen timer and held edits
        self.sub = None             # a page reached from another: forecast, timer, schedule
        self.power_view = "flow"    # or "now": the figures; "day": the last 24 hours
        self.flow_live = None       # [(edge, colour, px/s)] while the flow is on screen
        self.heat_live = None       # [(part, box)] while the heater diagram moves
        self.heater_view = "flow"   # the animated diagram; or "dial"
        self.fan_level = None       # the fan speed (1-4) chosen here; None: the heater's own
        self.switches = [False] * 6  # the Switches page, kept by the hub for every screen
        self._seg = None            # a page's segmented control in the header
        self._heat_now = None
        self._fgeo = None
        self._frect = None
        self._flut = {}             # connection -> its points, a pixel apart
        self._ht = None             # the heater diagram's frames, worked out once
        self.history = None         # [t, V, A, soc, W] rows from the hub's long history
        self._pending = {}          # settings changed here, shown until the hub agrees
        self.kt_set = 300           # kitchen timer: the time set, seconds
        self.kt_end = None          # mono time it finishes, while running
        self.kt_paused = None       # seconds left, while paused
        self.kt_done = False        # finished and not yet acknowledged
        self.clock_view = "timer"   # the Clock page: "timer" or "alarm"
        # hue 0-360 and saturation 0-100 from the colour wheel; "warm" is the
        # warm-white button; "cycle" slowly turns the hue round the wheel
        self.ambient = {"on": False, "h": 30, "s": 80, "bright": 50, "cycle": False,
                        "warm": False}
        self.wheel_img = None       # the colour wheel, built in the background
        self.panic = False          # beacon and siren running
        self.panic_arming = None    # mono ms the Panic button was pressed, while held
        # warm: minutes before the alarm the hub starts the heater (0 = not)
        self.alarm_clock = {"on": False, "at": "07:00", "days": "every", "warm": 0}
        self.night_src = None       # (sunset, sunrise, where from) behind night mode
        self.ac_ringing = False
        self.ac_snooze = None       # unix time a snoozed alarm rings again
        # heater controls
        self.target = None          # set point being chosen, degrees C
        self.water = 2              # 1 = 40, 2 = 60, 3 = boost
        self.confirm = None         # (action, [lines]) while asking
        self.pair = None            # {"code", "done"} while pairing with the hub
        self.busy = None            # text while a command is in flight
        self.result = None          # (text, colour) after one finishes
        self.dirty = True
        self._hits = []

    # -- time --------------------------------------------------------------------

    def hm(self, t):
        p = self.parts(t)
        return "%02d:%02d" % (p[3], p[4])

    def parts(self, t=None):
        if t is None:
            t = self.now() if self.now else None
        if t is None:
            return None
        return local_parts(t, self.tz[0], self.tz[1])

    def clock(self, t=None):
        p = self.parts(t)
        return (p[3], p[4]) if p else None

    def all_alerts(self):
        if self.alerts is not None:
            return self.alerts
        return (self.data or {}).get("alerts") or []

    def alarm(self):
        """The first alert nobody has cleared yet, or None."""
        for a in self.all_alerts():
            if not a.get("acked"):
                return a
        return None

    # -- input -----------------------------------------------------------------

    def tap(self, x, y):
        """Returns ("heater", action) when a confirmed command should be sent,
        otherwise None. Marks the screen dirty whenever anything changed."""
        self.hit = None
        self._tap = (x, y)
        for x0, y0, w, h, what in reversed(self._hits):
            if x0 <= x < x0 + w and y0 <= y < y0 + h:
                self.dirty = True
                self.hit = what
                return self._do(what)
        return None

    def hit_at(self, x, y):
        """What a touch at (x, y) would land on, without acting on it - for
        the Panic button, which acts on being held, not on release."""
        for x0, y0, w, h, what in reversed(self._hits):
            if x0 <= x < x0 + w and y0 <= y < y0 + h:
                return what
        return None

    # -- sliders ----------------------------------------------------------------
    # Things set by sliding a finger: the screen brightness, the ambient
    # light's brightness, and its colour wheel. main.py calls slide() for every
    # point while the finger is down on one, and hands the result on as for a
    # tap; the hub is told once, when the finger lifts.

    SLIDES = ("slide", "wheel", "dial")

    def _slider(self, key, x, y, w, h, frac, col):
        """A level bar with a knob, which a finger can drag or tap along.
        Its touch area is generous: well above and below the bar itself."""
        frac = 0.0 if frac < 0 else 1.0 if frac > 1 else frac
        bar(self.fb, x, y, w, h, frac, col)
        kx, ky, kr = x + int(w * frac), y + h // 2, h // 2 + 6
        gfx.dot(self.fb, kx, ky, kr, gfx.BLACK)
        gfx.dot(self.fb, kx, ky, kr - 2, TXT)
        self._slides[key] = (x, w)
        self._hit(x - 14, y - 24, w + 28, h + 48, ("slide", key))

    def slide(self, what, x, y):
        """A finger at (x, y) on the slider what (from hit_at). Returns what
        tap() would, or None when the value has not changed."""
        if what[0] == "wheel":
            self._tap = (x, y)
            return self._do(what)
        if what[0] == "dial":
            # the heater's ring: the target follows the finger round it, held
            # at the ends rather than jumping across the gap at the bottom
            cx, cy, R, th = self.DIAL
            dx, dy = x - cx, y - cy
            if dx * dx + dy * dy < (R - th - 24) ** 2:
                return None                      # the middle of the dial: nothing
            frac = (math.degrees(math.atan2(dx, -dy)) + self.SWEEP / 2) / self.SWEEP
            if not -0.15 <= frac <= 1.15:
                return None                      # in the gap, nearer neither end
            frac = 0.0 if frac < 0 else 1.0 if frac > 1 else frac
            t = int(round(self.T_MIN + frac * (self.T_MAX - self.T_MIN)))
            if t == self._target():
                return None
            self.target = t
            self.dirty = True
            return ("dial", t)
        key = what[1]
        x0, w = self._slides.get(key, (0, 1))
        frac = (x - x0) / w
        frac = 0.0 if frac < 0 else 1.0 if frac > 1 else frac
        if key == "bright":
            b = 0.1 + 0.9 * frac
            b = round(b * 100) / 100
            if b == self.brightness:
                return None
            self.brightness = b
            self.dirty = True
            return ("brightness", b)
        if key == "amb":
            am = self.ambient
            v = 10 + int(round(frac * 18)) * 5          # 10 to 100, in fives
            if v == am.get("bright"):
                return None
            am["bright"] = v
            self.dirty = True
            return ("prefs", "ambient")
        return None

    def mono_ms(self):
        return int(self.mono() * 1000) if self.mono else 0

    def swipe(self, direction):
        if (self.confirm or self.bright_open or self.alarm() or self.kt_done
                or self.panic or self.ac_ringing):
            return
        self.sub = None
        # The logo sits between the last page and the first, with no tab of its
        # own: swipe back from Home, or on from the last page, to reach it.
        # Three taps on it open the games (main.logo_tap).
        n = len(self.pages)
        if self.logo_page:
            self.logo_page = False
            self.page = 0 if direction > 0 else n - 1
        elif self.page == 0 and direction < 0:
            self.logo_page = True
        elif self.page == n - 1 and direction > 0:
            self.logo_page = True
        else:
            self.page += direction
        self.dirty = True

    def _do(self, what):
        kind, arg = what
        if kind == "ack":
            for a in self.all_alerts():         # silent here at once
                if a.get("id") == arg:
                    a["acked"] = True
            return ("ack", arg)
        if kind == "g":
            self._g_do(arg)
            return None
        if kind == "lview":
            self.level_view = arg
            return None
        if kind == "amb":
            return self._amb(arg)
        if kind == "wheel":
            # the colour under the tap: angle is the hue, distance the paleness
            R = self.WHEEL_R
            cx, cy = 8 + 14 + R, BODY_Y + 8 + 34 + R
            dx, dy = self._tap[0] - cx, self._tap[1] - cy
            am = self.ambient
            am["h"] = int((math.degrees(math.atan2(dx, -dy)) + 360) % 360)
            am["s"] = max(0, min(100, int(math.sqrt(dx * dx + dy * dy) * 100 / R)))
            am["warm"] = am["cycle"] = False
            am["on"] = True                      # picking a colour means you want it
            return ("prefs", "ambient")
        if kind == "ac":
            return self._ac(arg)
        if kind == "clockview":
            self.clock_view = arg
            return None
        if kind == "panic_stop":
            self.panic = False
            return ("panic", False)
        if kind == "panic_hold":
            return None                          # started by holding, in main.py
        if kind == "psub":
            # Power's own sub-pages: the flow, the battery, the lights
            self.sub = arg
            if arg is None:
                self.power_view = "flow"
            return None
        if kind == "resume":
            return ("resume",)
        if kind == "sw":
            if arg is not None:
                self.switches[arg] = not self.switches[arg]
                return ("post", "/api/switches", {"i": arg, "on": self.switches[arg]})
            return None
        if kind == "hview":
            self.heater_view = arg
            return None
        if kind == "pview":
            self.power_view = arg
            return None
        if kind == "sub":
            self.sub = arg
            return None
        if kind == "fanlvl":
            self.fan_level = arg
            return None
        if kind == "kt":
            self._kt(arg)
            return None if arg == "ack" else ("prefs", "kitchen")
        if kind == "guard":
            g = dict(self.guard())
            g["on"] = not g.get("on")
            g["state"] = "arming" if g["on"] else "off"
            g["arming_s"] = 60
            self._hold("guard", g)
            return ("post", "/api/guard", {"on": g["on"]})
        if kind == "tm":
            return self._timer_edit(*arg)
        if kind == "ah":
            return self._ah_edit(*arg)
        if kind == "st":
            return self._st_edit(arg)
        if kind == "tick":
            if arg in self.ticks:
                self.ticks.discard(arg)
            else:
                self.ticks.add(arg)
            return ("prefs", "ticks")
        if kind == "night":
            self.night_mode = {"auto": "on", "on": "off"}.get(self.night_mode, "auto")
            return ("night", self.night_mode)
        if kind == "sound":
            self.sound_on = not self.sound_on
            return ("sound", self.sound_on)
        if kind == "screen_off":
            self.bright_open = False
            if self.offmode:                     # "Keep screen on": off mode ends
                self.offmode = False
                return ("prefs", "offmode")
            return ("screen_off",)
        if kind == "dim":
            self.dim_on = not self.dim_on
            return ("prefs", "dim")
        if kind == "restart":
            self.bright_open = False
            self.confirm = ("restart", ["Restart the display?",
                                        "It is back in about half a minute,",
                                        "and settings are kept."])
            return None
        if kind == "shutdown":
            self.bright_open = False
            self.confirm = ("shutdown", ["Shut the display down?",
                                         "Plug it in to charge to start it",
                                         "again - it wakes within a minute."])
            return None
        if kind == "bright":
            if arg == "open":
                self.bright_open = True
                self.confirm = None
            elif arg == "done":
                self.bright_open = False
            else:
                b = round(self.brightness * 10 + arg) / 10
                self.brightness = 0.1 if b < 0.1 else 1.0 if b > 1.0 else b
                return ("brightness", self.brightness)
            return None
        if kind == "tab":
            self.page = arg
            self.sub = None
            self.confirm = None
        elif kind == "pair":
            self.pair = None
            return ("pair_cancel",)
        elif kind == "confirm":
            action = self.confirm[0] if self.confirm else None
            self.confirm = None
            if arg and action == "shutdown":
                return ("shutdown",)
            if arg and action == "restart":
                return ("restart",)
            if arg and action:
                return ("heater", action)
        elif kind == "temp":
            self.target = max(5, min(35, self._target() + arg))
        elif kind == "dial":
            # a tap on the ring sets the target to the temperature under it
            cx, cy, R, th = self.DIAL
            dx, dy = self._tap[0] - cx, self._tap[1] - cy
            if dx * dx + dy * dy < (R - th - 24) ** 2:
                return None                      # the middle of the dial: nothing
            deg = math.degrees(math.atan2(dx, -dy))
            frac = (deg + self.SWEEP / 2) / self.SWEEP
            if 0 <= frac <= 1:
                self.target = int(round(self.T_MIN + frac * (self.T_MAX - self.T_MIN)))
        elif kind == "water":
            self.water = self.water % 3 + 1
        elif kind == "heat":
            if self.busy:
                return None
            if arg == "read":                 # a read changes nothing: no confirm
                return ("heater", "read")
            self.confirm = (arg, self._confirm_text(arg))
        return None

    def _confirm_text(self, act):
        t, wl = self._target(), WATER[self.water]
        if act == "off":
            return ["Turn the heater OFF?"]
        if act == "vent":
            return ["Run the fan only, speed %d?" % self._fan(),
                    "No flame and no heat: it moves air", "round the van."]
        if act == "air":
            lines = ["Start air heating at %d°C?" % t]
        elif act == "water":
            lines = ["Start water heating (%s)?" % wl]
        else:
            lines = ["Start water + air heating?", "Water %s, air %d°C" % (wl, t)]
        # the hub decides "auto" when the command arrives; say what it will pick
        mains = (self.data or {}).get("mains_live")
        return lines + ["Fuel: auto - " + ("electric (hook-up live)" if mains
                                           else "diesel (no hook-up)")]

    def heater_body(self, action):
        """The request main.py posts to /api/heater/cmd, matching the web page."""
        fan = self._fan()
        # "auto": electric on a live hook-up, diesel otherwise - chosen by the
        # hub, the same for every screen. Never a fixed 0, which would switch a
        # heater running on electric back to diesel.
        return {"action": action, "temp": self._target(), "water": self.water,
                "level": fan if 1 <= fan <= 4 else 1, "energy": "auto"}

    # -- state helpers ---------------------------------------------------------

    def _heater(self):
        return (self.data or {}).get("heater") or {}

    def _target(self):
        if self.target is None:
            s = self._heater().get("set_air_c")
            self.target = s if s and 5 <= s <= 35 else 20
        return self.target

    def adopt_heater(self, h):
        """Pick up the heater's own settings when a fresh reading arrives."""
        wc = h.get("water_code")
        if wc in (1, 2, 3):
            self.water = wc
        s = h.get("set_air_c")
        if s and 5 <= s <= 35 and not self.confirm:
            self.target = s

    def _hit(self, x, y, w, h, what):
        self._hits.append((x, y, w, h, what))

    # -- drawing: frame --------------------------------------------------------

    def _logo_img(self):
        """The logo from tools/make_logo.py, as (framebuffer, w, h)."""
        if self._logo is None:
            try:
                with open(self._logo_path, "rb") as f:
                    blob = bytearray(f.read())
                if blob[:4] != b"IMG1":
                    raise ValueError("not a logo file")
                w, h = struct.unpack_from("<HH", blob, 4)
                self._logo = (framebuf.FrameBuffer(memoryview(blob)[8:], w, h,
                                                   framebuf.RGB565), w, h)
            except (OSError, ValueError) as e:
                print("logo unavailable:", e)
                self._logo = False
        return self._logo

    def _screensaver(self):
        fb = self.fb
        fb.fill(BG)
        img = self._logo_img()
        if img:
            logo, w, h = img
            fb.blit(logo, (W - w) // 2, (H - h) // 2)
        else:
            self.f.lg.text(fb, "Camperlux", W // 2, H // 2 - 16, BRAND, BG, 1)

    def render(self):
        fb = self.fb
        self._hits = []
        if self.night != getattr(self, "_night_drawn", False):
            set_night(self.night)
            self._night_drawn = self.night
        # the screensaver is only the logo: no buttons (a tap just wakes it),
        # and an alarm never hides behind it - main.py wakes the screen for one
        if (self.saver or self.logo_page) and not self.alarm():
            self._screensaver()
            self.dirty = False
            return
        self._background()
        self._chip = None               # a page may add a button to the header
        self.flow_live = self.heat_live = None   # the animated pages set them
        name = self.sub or self.pages[self.page]
        if name == "power" and self.power_view == "day":
            name = "power_day"
        elif name == "power" and self.power_view == "flow":
            name = "power_flow"
        if name == "timer" and self.clock_view == "alarm":
            name = "alarm"
        self._title = None              # a page may put its verdict in the title
        self._seg = None
        getattr(self, "_page_" + name)()
        name = self.pages[self.page]
        if name == "power":
            cur = self.sub if self.sub in ("battery", "lights") else None
            self._seg = [(label, ("psub", key), key == cur) for label, key in POWER_SUBS]
            self._chip = None
        self._header(self._title or PAGE_TITLES[name])
        self._tabs()
        # A pop-up takes every tap: its buttons only. Without this a tap beside
        # the heater's confirm dialog could land on the buttons underneath it.
        alarm = self.alarm()
        if (alarm or self.panic or self.ac_ringing or self.kt_done or self.confirm or self.bright_open
                or self.pair):
            self._hits = []
            self.flow_live = self.heat_live = None   # nothing moves under a pop-up
        if self.panic:
            self._panic_screen()                 # panic first: you set it off on purpose
        elif alarm:
            self._alarm(alarm)
        elif self.ac_ringing:
            self._ac_ringing()
        elif self.kt_done:
            self._kt_done()
        elif self.pair:
            self._pair_modal()
        elif self.confirm:
            self._modal()
        elif self.bright_open:
            self._brightness()
        self.dirty = False

    # -- background, and the colour under a point of it ----------------------------

    BAND = 4                              # gradient bands, px: 80 fills a frame

    def _background(self):
        if self._bg_rows is None or self._bg_rows[0] != BG_TOP:
            n = H // self.BAND
            self._bg_rows = [BG_TOP] + [gfx.blend(BG_TOP, BG_BOT, i / (n - 1)) for i in range(n)]
        fb, rows = self.fb, self._bg_rows
        for i in range(H // self.BAND):
            fb.fill_rect(0, i * self.BAND, W, self.BAND, rows[i + 1])

    def bgc(self, y):
        """The background colour at height y - what text drawn straight onto
        the page must blend its edges into."""
        if self._bg_rows is None:
            return BG
        i = 1 + max(0, min(H - 1, int(y))) // self.BAND
        return self._bg_rows[min(i, len(self._bg_rows) - 1)]

    def _header(self, title):
        fb, f = self.fb, self.f
        alerts = self.all_alerts()
        back = self.bgc(17)
        if alerts:
            back = gfx.blend(BG, RED, 0.35)
            fb.fill_rect(0, 0, W, HEAD_H, back)
            title = alerts[0].get("title", "Alert")
        if title == "Home" and not alerts:
            p = self.parts()
            if p:
                title = "%s %d %s" % (DAYS[p[6]], p[2], MONTHS[p[1] - 1])
        # the right-hand end of the header opens the brightness control
        self._hit(W - 150, 0, 150, HEAD_H + 6, ("bright", "open"))
        x = W - 14
        hm = self.clock()
        if hm:
            x -= f.mdb.text(fb, "%02d:%02d" % hm, x, 7, TXT, back, 2) + 12
        if self.kt_end is not None and self.mono:
            left = self.kt_left()
            x -= f.mdb.text(fb, "%d:%02d" % (left // 60, left % 60), x, 7, BRAND, back, 2) + 12
        b = self.dbatt
        if b:
            bx = x - 26
            pct = b["pct"]
            col = GREEN if pct > 40 else AMBER if pct > 15 else RED
            fb.rect(bx, 11, 21, 12, MUTED)
            fb.fill_rect(bx + 21, 14, 2, 6, MUTED)
            w = max(1, pct * 17 // 100)
            fb.fill_rect(bx + 2, 13, w, 8, col)
            if b["state"] == "charging":                 # a bolt across it
                gfx.thick_line(fb, bx + 12, 9, bx + 8, 17, TXT, 2)
                gfx.thick_line(fb, bx + 8, 17, bx + 13, 17, TXT, 2)
                gfx.thick_line(fb, bx + 13, 17, bx + 9, 25, TXT, 2)
            x -= 30
        if self.aa:
            self._icon("sun", x - 18, 8, MUTED, bg=back)
        x -= 28
        col = {"ok": GREEN, "stale": AMBER}.get(self.link, RED)
        if self.link != "ok" and not alerts:
            x -= f.sm.text(fb, self.link_note, x, 9, col, back, 2) + 8
        if self.aa:
            self._icon("wifi", x - 18, 6, col, bg=back)
        x -= 26
        # a page's own header button - Timers on the Heater page
        tx = 16
        if self._seg and not alerts:
            # a segmented control: one pill, the chosen part lit
            tw = f.mdb.width(title)
            sx = 16 + tw + 12
            ws = [f.sm.width(label) + 22 for label, what, on in self._seg]
            total = sum(ws) + 4
            if sx + total <= x - 12:
                self._pill(sx, 5, total, 26, PANEL)
                cx = sx + 2
                for (label, what, on), sw in zip(self._seg, ws):
                    if on:
                        self._pill(cx, 7, sw, 22, BRAND)
                    f.sm.text(fb, label, cx + sw // 2, 9, gfx.BLACK if on else TXT,
                              BRAND if on else PANEL, 1)
                    self._hit(cx, 0, sw, HEAD_H + 4, what)
                    cx += sw
                x = sx + 6                           # the title fits: it was measured
        if self._chip and not alerts:
            chips = self._chip if isinstance(self._chip, list) else [self._chip]
            tw = f.mdb.width(title)
            first = cx0 = 16 + tw + 10
            for label, ic, what in chips:
                cw = f.sm.width(label) + 40
                if cx0 + cw > x - 12:
                    break
                self._pill(cx0, 5, cw, 26, PANEL)
                if self.aa:
                    self._icon(ic, cx0 + 10, 10, MUTED, 16, bg=PANEL)
                f.sm.text(fb, label, cx0 + 32, 9, TXT, PANEL)
                self._hit(cx0 - 4, 0, cw + 8, HEAD_H + 4, what)
                cx0 += cw + 8
            if cx0 > first:
                x = first + 6                        # the title fits: it was measured
        room = x - tx - 6
        while title and f.mdb.width(title) > room:
            title = title[:-2] + "\u2026"
        f.mdb.text(fb, title, tx, 7, TXT, back)

    TAB_ICONS = {"home": "home", "drive": "check", "power": "bolt", "heater": "flame",
                 "level": "level", "battery": "battery", "lights": "bulb",
                 "switches": "switch", "tanks": "drop"}

    def _tabs(self):
        key = (self.page, self.night, tuple(self.pages))
        c = getattr(self, "_tab_cache", None)
        if c is None or c[0] != key:
            buf = bytearray(W * (H - TAB_Y) * 2)
            img = framebuf.FrameBuffer(buf, W, H - TAB_Y, framebuf.RGB565)
            self._draw_tabs(img, AA(buf, W, H - TAB_Y, img))
            c = self._tab_cache = (key, img)
        self.fb.blit(c[1], 0, TAB_Y)
        tw = W // len(self.pages)
        for i in range(len(self.pages)):
            self._hit(i * tw, TAB_Y, tw, H - TAB_Y, ("tab", i))

    def _draw_tabs(self, fb, a):
        """The tab bar, into its own image (row 0 is TAB_Y on screen)."""
        f = self.f
        base = gfx.blend(BG_BOT, PANEL, 0.7)
        fb.fill_rect(0, 0, W, H - TAB_Y, base)
        fb.hline(0, 0, W, gfx.blend(PANEL, CARD_HI, 0.6))
        n = len(self.pages)
        tw = W // n
        for i, p in enumerate(self.pages):
            cx = i * tw + tw // 2
            on = i == self.page
            back = base
            if on:
                back = gfx.blend(base, BRAND, 0.16)
                a.rrect(cx - 34, 4, 68, 36, 12, back)
            col = BRAND if on else MUTED
            icons.draw(a, self.TAB_ICONS.get(p, "home"), cx - 9, 4, col)
            f.sm.text(fb, PAGE_TITLES[p], cx, 22, col, back, 1)

    def _icon(self, name, x, y, col, size=18, bg=None):
        """An icon, drawn smooth once for each colour, size and background and
        then just copied: an icon is a handful of shapes, and a page shows
        a dozen of them on every frame."""
        if self.aa is None:
            return
        bg = PANEL if bg is None else bg
        key = (name, col, size, bg)
        cache = self._icons
        img = cache.get(key)
        if img is None:
            if len(cache) > 96:
                cache.clear()
            s_ = size + 2
            buf = bytearray(s_ * s_ * 2)
            img = framebuf.FrameBuffer(buf, s_, s_, framebuf.RGB565)
            img.fill(bg)
            icons.draw(AA(buf, s_, s_, img), name, 1, 1, col, size)
            cache[key] = img
        self.fb.blit(img, int(x) - 1, int(y) - 1)

    def _disc(self, cx, cy, r, col, alpha=255):
        if self.aa:
            self.aa.disc(cx, cy, r, col, alpha)
        else:
            self.fb.ellipse(int(cx), int(cy), int(r), int(r), col, True)

    def _circle(self, cx, cy, r, col, width=1.2):
        if self.aa:
            self.aa.ring(cx, cy, r - width / 2, r + width / 2, col)
        else:
            self.fb.ellipse(int(cx), int(cy), int(r), int(r), col)

    def _pill(self, x, y, w, h, col):
        rrect(self.fb, x, y, w, h, h // 2, col)

    def _panel(self, x, y, w, h):
        """A pop-up's panel (the confirm, brightness and alarm dialogs)."""
        if self.aa:
            self.aa.rrect(x, y + 4, w, h, 18, gfx.BLACK, alpha=140, clip_top=y + h - 2)
        rrect(self.fb, x - 1, y - 1, w + 2, h + 2, 18, CARD_HI)
        rrect(self.fb, x, y, w, h, 17, PANEL)

    def _card(self, x, y, w, h, label=None):
        """A card: a soft shadow, a lit top edge, rounded corners."""
        if self.aa:
            self.aa.rrect(x, y + 3, w, h, 14, gfx.BLACK, alpha=110, clip_top=y + h - 2)
            rrect(self.fb, x, y, w, h, 14, CARD_HI)
            rrect(self.fb, x, y + 1, w, h - 1, 14, PANEL)
        else:
            rrect(self.fb, x, y, w, h, 10, PANEL)
        if label:
            self.f.sm.text(self.fb, label.upper(), x + 14, y + 10, MUTED, PANEL)

    def _button(self, x, y, w, h, text, what, fg=TXT, back=PANEL2, enabled=True, icon=None,
                big=False):
        """A rounded button; icon is an icons.py name shown before the text.
        The text takes the largest font that fits with the icon beside it."""
        fb = self.fb
        if not enabled:
            fg = gfx.blend(back, fg, 0.35)
        rrect(fb, x, y, w, h, min(14, h // 2), back)
        iw = 24 if (icon and self.aa) else 0
        room = w - 20 - iw
        order = (self.f.lg, self.f.mdb, self.f.md, self.f.sm) if big else (self.f.mdb, self.f.md, self.f.sm)
        font = self._fit(text, room, order) if text else None
        tw = font.width(text) if font else 0
        gx = x + (w - iw - tw) // 2                   # icon and text centred as one
        if iw:
            self._icon(icon, gx, y + (h - 18) // 2, fg, bg=back)
        if font:
            font.text(fb, text, gx + iw, y + (h - font.height) // 2, fg, back)
        if enabled:
            self._hit(x, y, w, h, what)

    @staticmethod
    def _fit(text, width, fonts):
        for font in fonts:
            if font.width(text) <= width:
                return font
        return fonts[-1]

    def _placeholder(self, title, note):
        fb, f = self.fb, self.f
        self._card(8, BODY_Y + 8, W - 16, TAB_Y - BODY_Y - 16)
        f.lg.text(fb, title, W // 2, 120, MUTED, PANEL, 1)
        f.md.text(fb, note, W // 2, 164, MUTED, PANEL, 1)

    def _modal(self):
        fb, f = self.fb, self.f
        # darken everything behind by redrawing it as a flat wash
        fb.fill_rect(0, 0, W, H, gfx.blend(BG_BOT, gfx.BLACK, 0.4))
        x, y, w, h = 50, 60, 380, 200
        self._panel(x, y, w, h)
        lines = self.confirm[1]
        head = self._fit(lines[0], w - 24, (f.lg, f.mdb))
        head.text(fb, lines[0], x + w // 2, y + 22, TXT, PANEL, 1)
        ty = y + 70
        for ln in lines[1:]:
            f.md.text(fb, ln, x + w // 2, ty, MUTED, PANEL, 1)
            ty += 26
        bw = (w - 36) // 2
        act = self.confirm[0]
        self._button(x + 12, y + h - 64, bw, 52, "Cancel", ("confirm", False))
        self._button(x + 24 + bw, y + h - 64, bw, 52,
                     {"off": "Turn off", "shutdown": "Shut down", "restart": "Restart"}.get(act, "Start"),
                     ("confirm", True), fg=TXT,
                     back=RED if act in ("off", "shutdown") else gfx.blend(PANEL2, GREEN, 0.55))

    def _pair_modal(self):
        """The hub wants its password: this display pairs instead, by a code
        typed into the hub's Settings."""
        fb, f = self.fb, self.f
        fb.fill_rect(0, 0, W, H, gfx.blend(BG_BOT, gfx.BLACK, 0.4))
        x, y, w, h = 50, 46, 380, 228
        self._panel(x, y, w, h)
        p = self.pair
        if p.get("done"):
            f.lg.text(fb, "Paired with the hub", x + w // 2, y + 70, GREEN, PANEL, 1)
            f.md.text(fb, "This display can change things now", x + w // 2, y + 124, MUTED, PANEL, 1)
            return
        f.mdb.text(fb, "Pair with the hub", x + w // 2, y + 16, TXT, PANEL, 1)
        code = p["code"]
        f.xl.text(fb, code[:3] + " " + code[3:], x + w // 2, y + 50, BRAND, PANEL, 1)
        f.sm.text(fb, "Enter this code in the hub Settings,", x + w // 2, y + 116, MUTED, PANEL, 1)
        f.sm.text(fb, "under Security, within three minutes", x + w // 2, y + 136, MUTED, PANEL, 1)
        self._button(x + (w - 170) // 2, y + h - 60, 170, 48, "Not now", ("pair", False))

    # -- Power -----------------------------------------------------------------

    # -- Power: the energy flow, animated -----------------------------------------
    #
    # The web page's diagram: the three sources on the left, the battery in the
    # middle, the loads and the starter battery on the right. The page is drawn
    # as usual; main.py keeps a copy of it and, between redraws, puts dots on
    # the live connections and moves them along - faster the more power flows.

    FX, FW = 8, 118                     # left column
    BX, BW = 178, 124                   # the battery
    RX = 354                            # right column (width FW)
    FLOW_EDGES = (
        # key, cubic Bezier from a node's edge to the battery's (or back)
        ("solar", (126, 80), (152, 80), (152, 118), (178, 118)),
        ("alt", (126, 157), (145, 157), (159, 157), (178, 157)),
        ("mains", (126, 234), (152, 234), (152, 196), (178, 196)),
        ("load", (302, 118), (328, 118), (328, 96), (354, 96)),
        ("starter", (302, 196), (328, 196), (328, 219), (354, 219)),
    )
    FLOW_SPACING = 14                   # px between the moving dots

    def _flow_geo(self):
        """Each connection as points along its curve, with the distance to
        each: worked out once."""
        if self._fgeo is None:
            g = {}
            for key, p0, p1, p2, p3 in self.FLOW_EDGES:
                pts, cum, tot = [], [], 0.0
                for i in range(25):
                    t = i / 24
                    u = 1 - t
                    a, b, c, d = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
                    x = a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0]
                    y = a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]
                    if pts:
                        tot += math.sqrt((x - pts[-1][0]) ** 2 + (y - pts[-1][1]) ** 2)
                    pts.append((x, y))
                    cum.append(tot)
                g[key] = (pts, cum, tot)
            self._fgeo = g
        return self._fgeo

    def flow_rect(self, key):
        """The box round a connection that its dots can reach."""
        r = self._frect.get(key) if self._frect else None
        if r is None:
            pts = self._flow_geo()[key][0]
            x0 = int(min(p[0] for p in pts)) - 5
            y0 = int(min(p[1] for p in pts)) - 5
            x1 = int(max(p[0] for p in pts)) + 6
            y1 = int(max(p[1] for p in pts)) + 6
            r = (x0, y0, x1 - x0, y1 - y0)
            if self._frect is None:
                self._frect = {}
            self._frect[key] = r
        return r

    @staticmethod
    def _along(geo, s):
        pts, cum, _ = geo
        for i in range(1, len(pts)):
            if cum[i] >= s:
                k = (s - cum[i - 1]) / ((cum[i] - cum[i - 1]) or 1)
                return (pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * k,
                        pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * k)
        return pts[-1]

    def _flow_edge(self, key, col, live):
        """A connection: a track, brighter when power flows, and an arrowhead."""
        a = self.aa
        pts = self._flow_geo()[key][0]
        track = gfx.blend(BG, col, 0.45) if live else LINE
        for i in range(0, len(pts) - 2, 2):
            a.line(pts[i][0], pts[i][1], pts[i + 2][0], pts[i + 2][1], 3, track)
        (x0, y0), (x1, y1) = pts[-3], pts[-1]
        dx, dy = x1 - x0, y1 - y0
        n = math.sqrt(dx * dx + dy * dy) or 1
        dx, dy = dx / n, dy / n
        a.poly(((x1 + dx * 2, y1 + dy * 2), (x1 - dx * 7 - dy * 5, y1 - dy * 7 + dx * 5),
                (x1 - dx * 7 + dy * 5, y1 - dy * 7 - dx * 5)), track)

    @staticmethod
    def flow_speed(watts):
        """Dot speed, px/s: as the web page, a slow crawl for a trickle and
        brisk for hundreds of watts."""
        return 52 / max(0.35, min(2.4, 160 / max(watts, 6)))

    def _flow_lut(self, key):
        """A connection's points one pixel apart along its curve, as whole
        numbers: worked out once, so a frame only looks positions up."""
        lut = self._flut.get(key)
        if lut is None:
            g = self._flow_geo()[key]
            n = int(g[2])
            xs, ys = array("h", [0] * (n + 1)), array("h", [0] * (n + 1))
            for i in range(n + 1):
                x, y = self._along(g, i)
                xs[i], ys[i] = int(x + 0.5), int(y + 0.5)
            lut = (xs, ys, n)
            self._flut[key] = lut
        return lut

    def flow_dots(self, ms):
        """Draw the moving dots at animation time ms onto the page.

        Whole numbers and table look-ups only. Worked out in floating point,
        each frame left some 5 KB of discarded numbers behind, and clearing
        those up every so often was a visible stutter."""
        fb, sp = self.fb, self.FLOW_SPACING
        for key, col, speed in self.flow_live or ():
            xs, ys, n = self._flow_lut(key)
            s = (ms * speed // 1000) % sp
            while s < n - 6:
                fb.ellipse(xs[s], ys[s], 3, 3, col, True)
                s += sp

    def _fnode_text(self, x, y, h, value, sub, on):
        """A node's figures: the part that changes with every reading."""
        fb, f = self.fb, self.f
        w = self.FW
        font = self._fit(value, w - 20, (f.mdb, f.md, f.sm))
        font.text(fb, value, x + 10, y + 27, TXT if on else MUTED, PANEL)
        while f.sm.width(sub) > w - 20:
            sub = sub[:-2] + "\u2026"
        f.sm.text(fb, sub, x + 10, y + h - 19, MUTED, PANEL)

    @staticmethod
    def starter_sub(st, rok, engine):
        """The starter card's second line: its charge, from the hub's reading
        of the resting voltage (main.py, _starter_status). Only at rest does
        the voltage tell the charge; charging or just after, it says so."""
        if not rok:
            return "Offline"
        if engine:
            return "Engine running"
        st = st or {}
        if st.get("state") == "charging":
            return "Charging"
        if st.get("soc") is None:
            return "Resting"
        if st.get("state") == "settling":
            return "About %d%%" % st["soc"]
        return "%d%% charge" % st["soc"]

    def _page_power_flow(self):
        fb, f, a = self.fb, self.f, self.aa
        d = self.data or {}
        r = d.get("renogy") or {}
        v = d.get("victron") or {}
        der = d.get("derived") or {}
        st = d.get("stats") or {}
        rok, vok, bok = bool(r.get("connected")), bool(v.get("connected")), bool(d.get("connected"))
        self._chip = ("Details", "battery", ("pview", "now"))

        def W_(w):
            return "%d W" % round(w or 0)

        solar = (r.get("solar_w") or 0) if rok else 0
        alt = (r.get("alt_w") or 0) if rok else 0
        mains = (v.get("power_w") or 0) if vok else 0
        load = der.get("load_w")
        engine = bool(d.get("engine")) if "engine" in d else self.engine_running()
        sa, sv = r.get("solar_a") or 0, r.get("solar_v") or 0
        nh = 71
        nodes = (
            (self.FX, BODY_Y + 8, "sun", "Solar", W_(solar) if rok else DASH,
             "Offline" if not rok else ("%.1f A \u00b7 %d V" % (sa, sv)) if sa > 0.05
             else ("%.1f V" % sv) if solar > 0.5 else "Low sun" if sv > 5 else "No sun",
             solar > 0.5, BRAND),
            (self.FX, BODY_Y + 85, "engine", "Alternator", W_(alt) if rok else DASH,
             "Offline" if not rok else ("%.1f A" % (r.get("alt_a") or 0)) if alt > 0.5
             else "Engine on" if engine else "Engine off",
             alt > 0.5 or engine, CYAN),
            (self.FX, BODY_Y + 162, "plug", "Mains", W_(mains) if vok else DASH,
             ("%.1f A" % (v.get("current") or 0)) if vok
             else ("Unplugged" if v.get("stale") else "Offline"),
             mains > 0.5, BLUE),
            (self.RX, BODY_Y + 24, "bulb", "Loads", W_(load) if load is not None else DASH,
             ("%.1f A" % (der.get("load_a") or 0)) if load is not None else "",
             (load or 0) > 0.5, AMBER),
            (self.RX, BODY_Y + 147, "battery", "Starter",
             ("%.1f V" % (r.get("alt_v") or 0)) if rok else DASH,
             self.starter_sub(d.get("starter"), rok, engine),
             engine, BLUE),
        )
        edges = (("solar", solar, GREEN), ("alt", alt, GREEN), ("mains", mains, GREEN),
                 ("load", load or 0, AMBER))

        # Everything but the figures - the cards, icons, titles, connections
        # and the battery's case - is kept once drawn, and reused until
        # something in it changes (a source coming on, night mode). Redrawing
        # it all with every reading froze the moving dots for a tenth of a
        # second or more every five seconds.
        key = ((self.night, bok, engine) + tuple(n[6] for n in nodes)
               + tuple(e[1] > 0.5 for e in edges))
        a0, a1 = BODY_Y * W * 2, TAB_Y * W * 2
        if self._buf is not None and self._fstatic_key == key:
            memoryview(self._buf)[a0:a1] = self._fstatic
        else:
            for x, y, ic, title, value, sub, on, col in nodes:
                self._card(x, y, self.FW, nh)
                self._icon(ic, x + 10, y + 7, col if on else MUTED, 18)
                f.sm.text(fb, title, x + 34, y + 9, TXT if on else MUTED, PANEL)
            # the connections, over the cards' edges so the arrowheads show
            for k, watts, col in edges:
                self._flow_edge(k, col, watts > 0.5)
            self._flow_edge("starter", BLUE, engine)
            self._flow_battery_case(bok)
            if self._buf is not None:
                if self._fstatic is None:
                    self._fstatic = bytearray(a1 - a0)
                self._fstatic[:] = memoryview(self._buf)[a0:a1]
                self._fstatic_key = key
        for x, y, ic, title, value, sub, on, col in nodes:
            self._fnode_text(x, y, nh, value, sub, on)
        self.flow_live = [(k, col, int(self.flow_speed(watts))) for k, watts, col in edges
                          if watts > 0.5]
        # the battery: the whole thing is the gauge, filled from the bottom to
        # its charge like the Home page's, with the figures inside it
        self._flow_battery(d, st, bok)

    def _flow_battery_geo(self):
        cy = BODY_Y + 8                                  # the terminal on top
        y = cy + 9
        return self.BX, y, self.BW, TAB_Y - 10 - y, cy

    def _flow_battery_case(self, bok):
        fb = self.fb
        x, y, w, h, cy = self._flow_battery_geo()
        rim = MUTED if bok else LINE
        rrect(fb, x + w // 2 - 20, cy, 40, 12, 3, rim)
        rrect(fb, x, y, w, h, 14, rim)
        rrect(fb, x + 3, y + 3, w - 6, h - 6, 12, PANEL)

    def _flow_battery(self, d, st, bok):
        fb, f = self.fb, self.f
        x, y, w, h, cy = self._flow_battery_geo()
        cx = x + w // 2
        self._hit(x, cy, w, h + 9, ("pview", "now"))    # tap it for the details
        soc = d.get("soc") if bok else None
        pct = max(0, min(100, soc or 0))
        col = GREEN if pct > 50 else AMBER if pct > 20 else RED
        # the charge: a deep tone of the level's colour, so white text on it
        # reads, with a bright line along its top like a liquid's surface
        top = y + h - 7
        if soc is not None:
            fh = int((h - 14) * pct / 100)
            fill = gfx.blend(PANEL, col, 0.7)
            if fh > 0:
                rrect(fb, x + 7, top - fh, w - 14, fh, 8 if fh >= 16 else 0, fill)
                if fh < h - 16:
                    fb.fill_rect(x + 9, top - fh, w - 18, 2, col)
            top -= fh

        def line(font, text, ty, fg):
            # the colour under the text, so its smoothed edges blend into it
            under = fill if soc is not None and ty + font.height // 2 >= top else PANEL
            font.text(fb, text, cx, ty, fg, under, 1)

        if not bok:
            line(f.md, "Offline", y + h // 2 - 12, MUTED)
            return
        charging = (d.get("current") or 0) > 0.05
        p = d.get("power_w") or 0
        if p > 0.5:
            net = "+%d W" % round(p)
            tt = st.get("time_to_full_h")
            when = ("Full" if pct >= 100 else _fits(f.sm, w - 18, _eta_options(tt, True))
                    if tt is not None else "Charging")
        elif p < -0.5:
            net = "\u2013%d W" % round(-p)          # the fonts have no U+2212 minus
            tt = st.get("time_to_empty_h")
            when = _fits(f.sm, w - 18, _eta_options(tt, False)) if tt is not None else "Discharging"
        else:
            net, when = "0 W", "Full" if pct >= 100 else "Resting"
        if charging:
            self._bolt(cx - 9, y + 12, 22, GREEN if top > y + 40 else TXT)
        sw = f.xl.width("%d" % pct) + f.md.width("%") + 2
        sx = cx - sw // 2
        under = fill if y + 60 >= top else PANEL
        n = f.xl.text(fb, "%d" % pct, sx, y + 38, TXT, under)
        f.md.text(fb, "%", sx + n + 2, y + 52, MUTED if under == PANEL else TXT, under)
        line(f.sm, "%s V" % _num(d.get("voltage"), "%.2f"), y + 92, MUTED if y + 99 < top else TXT)
        # a four-figure load (a kettle on the inverter) is too wide for the
        # large font: step down, and keep the line where it was
        font = self._fit(net, w - 16, (f.lg, f.mdb, f.md))
        line(font, net, y + 116 + (f.lg.height - font.height) // 2, TXT)
        font = self._fit(when, w - 18, (f.sm,))
        line(font, when, y + 154, TXT if y + 160 >= top else MUTED)

    def _page_power(self):
        fb, f = self.fb, self.f
        d = self.data or {}
        self._chip = ("Flow", "bolt", ("pview", "flow"))
        # battery card
        x, y, w, h = 8, BODY_Y + 8, 224, TAB_Y - BODY_Y - 16
        self._card(x, y, w, h, "Battery")
        self._button(x + w - 66, y + 6, 58, 26, "24 h", ("pview", "day"))
        if not d.get("connected"):
            f.md.text(fb, "Not connected", x + 12, y + 44, MUTED, PANEL)
            if d.get("soc") is not None:
                f.sm.text(fb, "last %d%%" % d["soc"], x + 12, y + 72, MUTED, PANEL)
        else:
            soc = d.get("soc")
            col = GREEN if (soc or 0) > 50 else AMBER if (soc or 0) > 20 else RED
            sw = f.xxl.text(fb, _num(soc, "%d"), x + 12, y + 34, TXT, PANEL)
            f.lg.text(fb, "%", x + 16 + sw, y + 34, MUTED, PANEL)
            bar(fb, x + 12, y + 130, w - 24, 12, (soc or 0) / 100, col)
            cur = d.get("current") or 0.0
            if cur > 0.05:
                state, scol = "Charging", GREEN
                cap = (d.get("nominal_ah") or 0) - (d.get("residual_ah") or 0)
                eta = "Full in " + _hm(cap / cur) if cap > 0 else "Full"
            elif cur < -0.05:
                state, scol = "Discharging", AMBER
                eta = _hm((d.get("residual_ah") or 0) / -cur) + " left"
            else:
                state, scol, eta = "Resting", MUTED, "No current flowing"
            f.mdb.text(fb, state, x + 12, y + 154, scol, PANEL)
            f.sm.text(fb, eta, x + 12, y + 178, MUTED, PANEL)
            f.md.text(fb, "%s V   %s A" % (_num(d.get("voltage"), "%.2f"),
                                          _num(cur, "%+.1f")),
                      x + 12, y + 202, TXT, PANEL)
        # four tiles
        r = d.get("renogy") or {}
        v = d.get("victron") or {}
        der = d.get("derived") or {}
        tiles = (
            ("Solar", r.get("connected"), r.get("solar_w"), BRAND,
             _num(r.get("solar_v"), "%.1f V")),
            ("Alternator", r.get("connected"), r.get("alt_w"), CYAN,
             _num(r.get("alt_v"), "%.1f V")),
            ("Mains", v.get("connected"), v.get("power_w"), BLUE,
             v.get("state") or ""),
            ("Load", "load_w" in der, der.get("load_w"), TXT,
             _num(der.get("load_a"), "%.1f A")),
        )
        th = (TAB_Y - BODY_Y - 16 - 3 * 6) // 4
        for i, (label, ok, watts, col, sub) in enumerate(tiles):
            tx, tw = 240, 232
            ty = BODY_Y + 8 + i * (th + 6)
            self._card(tx, ty, tw, th)
            f.mdb.text(fb, label, tx + 12, ty + 7, TXT, PANEL)
            if not ok:
                f.sm.text(fb, "Not connected", tx + 12, ty + 31, MUTED, PANEL)
                f.lg.text(fb, DASH, tx + tw - 14, ty + 12, MUTED, PANEL, 2)
                continue
            f.sm.text(fb, sub, tx + 12, ty + 31, MUTED, PANEL)
            active = (watts or 0) >= 1
            uw = f.md.text(fb, "W", tx + tw - 12, ty + 20, MUTED, PANEL, 2)
            f.lg.text(fb, _num(watts, "%d"), tx + tw - 16 - uw, ty + 12,
                      col if active else MUTED, PANEL, 2)

    # -- Heater ----------------------------------------------------------------
    #
    # A thermostat: a dial whose arc fills from 5 C to the target in a warm
    # gradient, glowing while the heater runs; the target large in the middle;
    # the cabin temperature marked on the ring. The mode buttons down the right
    # start it; every start still asks first.

    DIAL = (150, 152, 104, 16)          # centre x, y, outer radius, ring thickness
    T_MIN, T_MAX = 5, 35
    SWEEP = 270                          # degrees of ring, gap at the bottom
    GRAD_STEPS = 14                      # slices in the dial's colour gradient

    def _t_to_angle(self, t):
        """Temperature to a dial angle, radians clockwise from 12 o'clock."""
        f = (t - self.T_MIN) / (self.T_MAX - self.T_MIN)
        f = 0.0 if f < 0 else 1.0 if f > 1 else f
        return math.radians(-self.SWEEP / 2 + f * self.SWEEP)

    def _mode(self, h):
        """Which start button matches what the heater is doing, if any."""
        if not h.get("on"):
            return None
        return {4: "air", 3: "combi", 5: "water", 6: "vent"}.get(h.get("mode_code"))

    def _fan(self):
        """The fan speed the next command carries: the one chosen on the Fan
        page, else what the heater last reported."""
        if self.fan_level:
            return self.fan_level
        f = self._heater().get("fan_level") or 1
        return f if 1 <= f <= 4 else 1

    def _round_button(self, cx, cy, r, icon, what, enabled=True):
        a = self.aa
        fg = TXT if enabled else gfx.blend(PANEL, TXT, 0.35)
        a.disc(cx, cy + 2, r, gfx.BLACK, alpha=100)
        a.disc(cx, cy, r, CARD_HI)
        a.disc(cx, cy, r - 1, PANEL)
        self._icon(icon, cx - 9, cy - 9, fg, bg=PANEL)
        if enabled:
            self._hit(cx - r - 4, cy - r - 4, 2 * r + 8, 2 * r + 8, what)

    # -- Heater: the diagram, animated -----------------------------------------------
    #
    # The web Heater page's scene, fitted to the left of the controls: the
    # heater with its burner, the water tank and the hot water above, the fan
    # and the cabin below. What moves shows what the heater is doing: a
    # flickering flame, a glowing element, the fan turning, warm air and hot
    # water running along the pipes, bubbles in the tank, exhaust puffs.

    HEAT_SCENE = {3: ("burn", "air", "water"), 4: ("burn", "air"), 5: ("burn", "water"),
                  6: ("vent",), 7: ("burn",), 8: ("air",)}
    HEAT_CAPTION = {3: "Hot water and warm air", 4: "Warm air to the cabin",
                    5: "Heating the water tank", 6: "Fan only, no flame",
                    7: "Priming the fuel line", 8: "Shutting down, fan cooling",
                    10: "Standby, everything off"}
    # the boxes that change from frame to frame
    HB = {"flame": (30, 94, 48, 58), "fan": (125, 169, 50, 50),
          "air1": (100, 187, 26, 14), "air2": (175, 187, 15, 14),
          "water1": (100, 77, 26, 14), "water2": (196, 77, 20, 14),
          "bubbles": (131, 74, 60, 52), "puff": (38, 244, 32, 30)}

    def _heater_flow(self, h, have):
        fb, f, a = self.fb, self.f, self.aa
        code = h.get("mode_code") if have else None
        parts = self.HEAT_SCENE.get(code, ()) if have else ()
        energy = h.get("energy_code") or 0
        burn = "burn" in parts and energy < 3             # the diesel flame
        elec = "burn" in parts and energy >= 1            # the electric element
        fan = "air" in parts or "vent" in parts
        water = "water" in parts
        well = gfx.blend(PANEL, gfx.BLACK, 0.55)
        dim = gfx.blend(PANEL, TXT, 0.35)

        # the heater, with its burner well
        self._card(8, BODY_Y + 8, 92, 190)
        f.sm.text(fb, "Heater", 54, BODY_Y + 15, MUTED, PANEL, 1)
        if burn or elec:
            a.disc(54, 124, 38, FLAME, alpha=50)
        a.disc(54, 123, 31, LINE)
        a.disc(54, 123, 29, well)
        if not (burn or elec):                            # cold: a pilot mark
            a.disc(54, 132, 4, dim)
        st = ("Burning" if burn else "Electric" if elec else
              "Burner off" if have else "Not read")
        font = self._fit(st, 84, (f.sm,))
        font.text(fb, st, 54, 164, FLAME if (burn or elec) else MUTED, PANEL, 1)
        lvl = h.get("fan_level") or 1
        f.sm.text(fb, ("Fan %d" % lvl) if fan else "Fan off", 54, 186,
                  TXT if fan else MUTED, PANEL, 1)
        fb.fill_rect(50, BODY_Y + 198, 8, 10, LINE)       # the exhaust

        # water above: pipe, tank, pipe, hot water
        hot = gfx.blend(BG, FLAME, 0.5)
        for x0, x1 in ((100, 126), (196, 216)):
            fb.fill_rect(x0, 81, x1 - x0, 6, hot if water else LINE)
        self._card(126, BODY_Y + 8, 70, 86)
        wt = h.get("water_temp_c") if have else None
        level = {1: 0.55, 2: 0.7, 3: 0.85}.get(h.get("water_code"), 0.6) if water else 0.45
        wh = int(76 * level)
        fb.fill_rect(131, 126 - wh, 60, wh, gfx.blend(PANEL, BLUE, 0.55))
        fb.fill_rect(131, 126 - wh, 60, 2, BLUE)
        f.md.text(fb, (_num(wt, "%d") + "\u00b0") if wt is not None else DASH, 161, 52,
                  TXT, PANEL, 1)
        self._card(216, BODY_Y + 8, 76, 86)
        f.sm.text(fb, "Water", 224, 52, MUTED, PANEL)
        wc = h.get("water_code")
        f.mdb.text(fb, ({1: "40\u00b0", 2: "60\u00b0", 3: "Boost"}.get(wc, "On") if water
                        else "Off"), 224, 74, TXT if water else MUTED, PANEL)
        tgt = {1: 40, 2: 60}.get(wc)
        sub = ("" if not water else "Ready" if (tgt and wt is not None and wt >= tgt)
               else "Heating")
        f.sm.text(fb, sub, 224, 100, FLAME if sub == "Heating" else MUTED, PANEL)

        # air below: pipe, fan, pipe, cabin
        air = FLAME if "air" in parts else CYAN
        for x0, x1 in ((100, 126), (174, 190)):
            fb.fill_rect(x0, 191, x1 - x0, 6, gfx.blend(BG, air, 0.5) if fan else LINE)
        a.disc(150, 194, 25, LINE)
        a.disc(150, 194, 23, well)
        if not fan:
            self._fan_blades(0, dim)                      # still
        self._card(190, 156, 102, 78)
        f.sm.text(fb, "Cabin", 198, 162, MUTED, PANEL)
        cab = h.get("air_temp_c") if have else None
        f.lg.text(fb, (_num(cab, "%d") + "\u00b0") if cab is not None else DASH, 198, 179,
                  TXT, PANEL)
        on = bool(have and h.get("on"))
        f.sm.text(fb, ("to %d\u00b0" % h["set_air_c"]) if on and h.get("set_air_c")
                  else "at the heater", 198, 212, MUTED, PANEL)

        # what it is doing, in words
        cap = self.HEAT_CAPTION.get(code, "Running" if on else "Standby") if have \
            else "Not read yet: tap ↻"
        if self.busy:
            cap = self.busy
        elif self.result:
            cap = self.result[0]
        while f.sm.width(cap) > 186:
            cap = cap[:-2] + "\u2026"
        f.sm.text(fb, cap, 196, 135, MUTED, self.bgc(142), 1)

        # the target, as on the dial: chosen here, sent with the next heat button
        f.sm.text(fb, "Heat to", 164, 250, MUTED, self.bgc(257), 2)
        self._stepper(172, 242, "%d\u00b0" % self._target(), ("temp", -1), ("temp", 1),
                      w=58, enabled=not self.busy)

        live = []
        for key, want in (("flame", burn or elec), ("fan", fan), ("air1", fan), ("air2", fan),
                          ("water1", water), ("water2", water), ("bubbles", water),
                          ("puff", burn)):
            if want:
                live.append((key, self.HB[key]))
        self._heat_now = (burn, elec, lvl, air)
        self.heat_live = live or None

    def _fan_blades(self, ang, col):
        # plain (unsmoothed) shapes: they are redrawn up to 16 times a second,
        # and the smooth kind cost several times as much
        fb = self.fb
        for i in range(4):
            t = ang + i * 1.5708
            fb.poly(0, 0, array("h", (150, 194,
                                      int(150 + math.sin(t) * 20), int(194 - math.cos(t) * 20),
                                      int(150 + math.sin(t + 0.55) * 18),
                                      int(194 - math.cos(t + 0.55) * 18))), col, True)
        fb.ellipse(150, 194, 4, 4, col, True)

    ELEMENT = (36, 142, 42, 142, 46, 134, 51, 150, 56, 134, 61, 150, 66, 134, 70, 142)

    def _heat_tables(self):
        """Everything that moves on the heater diagram, worked out once for a
        loop of frames: the flame's outline for 4 s at 10 frames a second,
        the fan's blades a few degrees apart, the element's glow and the
        exhaust's colours. A frame then only picks from these. (Rebuilt when
        night mode changes the colours.)"""
        if self._ht is not None and self._ht[0] == self.night:
            return self._ht
        flames = []
        tau = 2 * math.pi
        for i in range(40):
            t = i / 10
            k = 1 + 0.08 * math.sin(tau * t * 3 / 4) + 0.05 * math.sin(tau * t * 5 / 4)
            sw = int(2.5 * math.sin(tau * t / 2))
            tp = int(123 + 22 - 50 * k)
            flames.append((
                array("h", (54, 149, 45, 146, 39, 138, 38, 128, 42, 118, 48, 110,
                            52 + sw // 2, 102, 54 + sw, tp, 57 + sw // 2, 104, 62, 112,
                            67, 121, 70, 131, 67, 142, 61, 148)),
                array("h", (54, 147, 48, 143, 46, 135, 48, 127, 52, 119,
                            54 + (sw * 3) // 5, tp + 20, 57, 121, 61, 129, 61, 139, 58, 145))))
        fans = []
        for j in range(30):                      # a quarter turn: the blades repeat
            ang = j * (math.pi / 2) / 30
            blades = []
            for i in range(4):
                t = ang + i * math.pi / 2
                blades.append(array("h", (150, 194,
                                          int(150 + math.sin(t) * 20), int(194 - math.cos(t) * 20),
                                          int(150 + math.sin(t + 0.55) * 18),
                                          int(194 - math.cos(t + 0.55) * 18))))
            fans.append(blades)
        glow0 = gfx.blend(PANEL, EMBER, 0.5)
        elem = [gfx.blend(glow0, EMBER, 0.55 + 0.45 * math.sin(tau * i / 16)) for i in range(16)]
        wobble = array("b", [int(3 * math.sin(tau * i / 64)) for i in range(64)])
        puff = [gfx.blend(BG, MUTED, 0.8 - (i / 8) * 0.6) for i in range(8)]
        self._ht = (self.night, flames, fans, elem, wobble, puff,
                    FLAME, gfx.blend(FLAME, TXT, 0.55), gfx.blend(PANEL, TXT, 0.7),
                    gfx.blend(BLUE, TXT, 0.7))
        return self._ht

    def _heat_frame(self, ms):
        """The moving parts at animation time ms, over the page as drawn.
        Whole numbers and look-ups only, for the reason given at flow_dots."""
        fb = self.fb
        (night, flames, fans, elem, wobble, puff, c_flame, c_core, c_fan,
         c_bubble) = self._heat_tables()
        burn, elec, lvl, air = self._heat_now
        for key, box in self.heat_live or ():
            if key == "flame":
                if burn:
                    outer, inner = flames[(ms // 100) % 40]
                    fb.poly(0, 0, outer, c_flame, True)
                    fb.poly(0, 0, inner, c_core, True)
                if elec:
                    col = elem[(ms // 60) % 16]
                    p = self.ELEMENT
                    for i in range(0, 14, 2):
                        for dy in (-1, 0, 1):
                            fb.line(p[i], p[i + 1] + dy, p[i + 2], p[i + 3] + dy, col)
            elif key == "fan":
                # (2 + level) radians a second; 30 steps to the quarter turn
                for blade in fans[(ms * (2 + lvl) * 30 // 1571) % 30]:
                    fb.poly(0, 0, blade, c_fan, True)
                fb.ellipse(150, 194, 4, 4, c_fan, True)
            elif key == "bubbles":
                for i in range(5):
                    ph = (ms * 6 // 10 + i * 210) % 1000
                    r = 2 + (i % 2)
                    fb.ellipse(139 + i * 11 + wobble[(ms // 50 + i * 10) % 64],
                               122 - ph * 44 // 1000, r, r, c_bubble, True)
            elif key == "puff":
                for i in range(3):
                    ph = (ms * 7 // 10 + i * 333) % 1000
                    r = 3 + ph * 6 // 1000
                    fb.ellipse(54 + ph * 4 // 1000, 248 + ph * 18 // 1000, r, r,
                               puff[ph * 8 // 1000], True)
            else:                                # the pipes' moving dashes
                x, y, w, h = box
                col = air if (key == "air1" or key == "air2") else c_flame
                xx = x - 12 + (ms * 45 // 1000) % 12
                while xx < x + w:
                    x0 = xx if xx > x else x
                    x1 = xx + 6 if xx + 6 < x + w else x + w
                    if x1 > x0:
                        fb.fill_rect(x0, y + 4, x1 - x0, 6, col)
                    xx += 12

    def page_sig(self):
        """For a page that shows only a small part of the hub's data, that
        part - so a reading that leaves it unchanged needs no redraw. None:
        redraw on every reading. The heater is read only when asked, so on its
        page almost every reading is the same, and each redraw paused the
        diagram's animation."""
        if self.sub is None and not self.logo_page and self.pages[self.page] == "heater":
            d = self.data or {}
            return (repr(d.get("heater")), repr(d.get("alerts")), d.get("mains_live"),
                    repr(d.get("autoheat")))
        return None

    def animating(self):
        return bool(self.flow_live or self.heat_live)

    def anim_boxes(self):
        """(key, box) for everything that moves on the page as drawn."""
        if self.flow_live:
            return [(e[0], self.flow_rect(e[0])) for e in self.flow_live]
        return self.heat_live or []

    def anim_draw(self, ms):
        """The moving parts at animation time ms (a whole number)."""
        if self.flow_live:
            self.flow_dots(ms)
        elif self.heat_live:
            self._heat_frame(ms)

    # -- Switches: six circuits, for relays on the hub later ------------------------
    #
    # A mock-up for now: the buttons turn on and off on the screen only. When
    # the relays are wired to the hub, each button will send its switch and
    # show what the hub reports back.

    SWITCHES = (("Water pump", "drop"), ("Fridge", "snow"), ("Garage light", "home"),
                ("Inverter", "bolt"), ("Lights", "bulb"), ("USB", "power"))

    def _switch_names(self):
        """The buttons' names as set in the hub's settings; the defaults
        until the hub has said, or if it sends something unusable."""
        n = (self.data or {}).get("switch_names")
        if isinstance(n, list) and len(n) == len(self.SWITCHES):
            return [x if isinstance(x, str) and x else d[0] for x, d in zip(n, self.SWITCHES)]
        return [d[0] for d in self.SWITCHES]

    def _switch_icons(self):
        """The buttons' icons as chosen in the hub's settings, else the defaults."""
        n = (self.data or {}).get("switch_icons")
        if isinstance(n, list) and len(n) == len(self.SWITCHES):
            return [x if isinstance(x, str) and x else d[1] for x, d in zip(n, self.SWITCHES)]
        return [d[1] for d in self.SWITCHES]

    # -- G-force (the Level page, while driving) -------------------------------
    # An accelerometer cannot tell a tilt from an acceleration: braking reads as
    # the nose dipping, a bend as a lean, a hill as accelerating. So rather than
    # drawing a van doing wheelies, this view turns the apparent tilt back into
    # the force felt: longitudinal g = tan(pitch), lateral g = tan(roll),
    # measured from the levelling calibration (so a slope reads as a little
    # acceleration), and bumps from how far the total strays from the sensor's
    # steady 1 g.

    G_SCALE = 0.6               # the g-ball's edge and the bars' ends, in g

    def gforce_sample(self, l):
        """A fresh /api/level reading, while the G-force view is showing."""
        if not l or not l.get("ok"):
            return
        lat = math.tan(math.radians(l.get("roll") or 0.0))
        lon = math.tan(math.radians(l.get("pitch") or 0.0))
        ax = l.get("axes") or {}
        mag = math.sqrt(sum((ax.get(k) or 0.0) ** 2 for k in ("x", "y", "z")))
        vert = 0.0
        if mag:
            self.g_base = mag if self.g_base is None else self.g_base + 0.02 * (mag - self.g_base)
            vert = mag - self.g_base
        self.g_now = (lat, lon, vert)
        self.g_trail = (self.g_trail + [(lat, lon)])[-24:]
        pk = self.g_peak
        pk["acc"] = max(pk["acc"], lon)
        pk["brk"] = max(pk["brk"], -lon)
        pk["right"] = max(pk["right"], lat)
        pk["left"] = max(pk["left"], -lat)
        pk["bump"] = max(pk["bump"], abs(vert))
        self.dirty = True

    def _g_do(self, arg):
        """The peaks card: start the peaks again."""
        for k in self.g_peak:
            self.g_peak[k] = 0.0

    def _page_gforce(self):
        fb, f, a = self.fb, self.f, self.aa
        self._title = "G-force"
        self._chip = ("Level", "level", ("sub", None))
        S = self.G_SCALE
        lat, lon, vert = self.g_now or (0.0, 0.0, 0.0)
        pk = self.g_peak

        # the g-ball: where the push is, like a marble in a bowl
        cx, cy, R = 118, BODY_Y + 121, 108
        self._card(8, BODY_Y + 8, 220, TAB_Y - BODY_Y - 16)
        if a:
            a.disc(cx, cy, R - 4, PANEL2)
            for g in (0.2, 0.4):
                a.ring(cx, cy, (R - 4) * g / S - 1, (R - 4) * g / S + 1, LINE)
        fb.hline(cx - R + 8, cy, 2 * R - 16, LINE)
        fb.vline(cx, cy - R + 8, 2 * R - 16, LINE)
        f.sm.text(fb, "ACCEL", cx, cy - R + 10, MUTED, PANEL2, 1)
        f.sm.text(fb, "BRAKE", cx, cy + R - 28, MUTED, PANEL2, 1)
        f.sm.text(fb, "L", cx - R + 14, cy - 18, MUTED, PANEL2)
        f.sm.text(fb, "R", cx + R - 22, cy - 18, MUTED, PANEL2)
        f.sm.text(fb, "0.2", cx + int((R - 4) * 0.2 / S) + 2, cy + 2, MUTED, PANEL2)

        def at(gx, gy):
            m = math.sqrt(gx * gx + gy * gy)
            if m > S:                       # off the scale: held at the rim
                gx, gy = gx * S / m, gy * S / m
            return cx + (R - 12) * gx / S, cy - (R - 12) * gy / S

        n = len(self.g_trail)
        for i, (tx, ty) in enumerate(self.g_trail[:-1]):
            px, py = at(tx, ty)
            if a:
                a.disc(px, py, 2 + 3 * i / max(1, n), gfx.blend(PANEL2, BRAND, 0.25 + 0.5 * i / max(1, n)))
        px, py = at(lat, lon)
        tot = math.sqrt(lat * lat + lon * lon)
        col = GREEN if tot < 0.25 else AMBER if tot < 0.45 else RED
        if a:
            a.disc(px, py + 2, 11, gfx.BLACK, alpha=110)
            a.disc(px, py, 10, col)
            a.disc(px, py, 4, TXT)

        # the bars: each force on its own, with its peak
        x0, w = 236, W - 236 - 8
        # (label, value, peak key one way, the other way, words for + and -)
        rows = (("Forward", lon, "acc", "brk", ("accel", "brake")),
                ("Cornering", lat, "right", "left", ("right", "left")),
                ("Bumps", vert, "bump", None, None))
        y = BODY_Y + 8
        rh = 48
        for k, (label, v, pk_pos, pk_neg, ends) in enumerate(rows):
            ry = y + k * (rh + 4)
            self._card(x0, ry, w, rh)
            f.sm.text(fb, label.upper(), x0 + 12, ry + 7, MUTED, PANEL)
            word = "" if ends is None or abs(v) < 0.02 else " " + (ends[0] if v > 0 else ends[1])
            f.md.text(fb, "%.2f g%s" % (abs(v), word), x0 + w - 12, ry + 4, TXT, PANEL, 2)
            bx, bw_, by, bh = x0 + 12, w - 24, ry + rh - 18, 10
            rrect(fb, bx, by, bw_, bh, 5, PANEL2)
            if pk_neg is None:                       # bumps: one-sided, 0 to 0.5 g
                fw = int(bw_ * min(1.0, abs(v) / 0.5))
                if fw > 0:
                    rrect(fb, bx, by, max(fw, bh), bh, 5, BLUE)
                mx = bx + int(bw_ * min(1.0, pk[pk_pos] / 0.5))
                fb.vline(mx, by - 3, bh + 6, TXT)
                continue
            mid = bx + bw_ // 2
            fb.vline(mid, by - 3, bh + 6, LINE)
            fw = int((bw_ // 2) * min(1.0, abs(v) / S))
            colb = GREEN if abs(v) < 0.25 else AMBER if abs(v) < 0.45 else RED
            if fw > 0:
                fb.fill_rect(mid if v > 0 else mid - fw, by + 1, fw, bh - 2, colb)
            # peaks either side
            for side, key in ((1, pk_pos), (-1, pk_neg)):
                mx = mid + side * int((bw_ // 2) * min(1.0, pk[key] / S))
                if pk[key] > 0.01:
                    fb.vline(mx, by - 3, bh + 6, TXT)

        # the peaks since they were last reset - the card itself resets them
        ry = y + 3 * (rh + 4)
        ph = TAB_Y - 8 - ry
        self._card(x0, ry, w, ph)
        f.sm.text(fb, "PEAKS", x0 + 12, ry + 6, MUTED, PANEL)
        f.sm.text(fb, "tap to reset", x0 + w - 12, ry + 6, MUTED, PANEL, 2)
        f.sm.text(fb, "Brake %.2f    Accel %.2f" % (pk["brk"], pk["acc"]), x0 + 12, ry + 26, TXT, PANEL)
        f.sm.text(fb, "Corner %.2f   Bump %.2f" % (max(pk["left"], pk["right"]), pk["bump"]),
                  x0 + 12, ry + 26 + f.sm.height + 2, TXT, PANEL)
        self._hit(x0, ry, w, ph, ("g", "reset"))

    # -- Tanks -------------------------------------------------------------------

    def _tank_levels(self):
        t = (self.data or {}).get("tanks")
        if isinstance(t, dict) and ("fresh" in t or "grey" in t):
            return t
        return self.tanks

    def _page_tanks(self):
        fb, f = self.fb, self.f
        t = self._tank_levels()
        self._title = "Tanks"
        if t.get("demo"):
            self._chip = ("Sample levels", "drop", ("noop", None))
        gap = 8
        cw = (W - 16 - gap) // 2
        ch = TAB_Y - BODY_Y - 16
        y = BODY_Y + 8
        for i, (key, name) in enumerate((("fresh", "Fresh water"), ("grey", "Grey water"))):
            x = 8 + i * (cw + gap)
            self._card(x, y, cw, ch, name)
            pct = t.get(key)
            self._tank(x + 14, y + 34, 84, ch - 48, pct, key)
            tx = x + 112
            if pct is None:
                f.lg.text(fb, "\u2013", tx, y + 60, MUTED, PANEL)
                f.sm.text(fb, "No reading", tx, y + 110, MUTED, PANEL)
                continue
            word, col = self._tank_state(key, pct)
            f.lg.text(fb, "%d%%" % pct, tx, y + 48, TXT, PANEL)
            f.sm.text(fb, "full", tx, y + 48 + f.lg.height + 2, MUTED, PANEL)
            pw = f.sm.width(word) + 24
            self._pill(tx - 2, y + 118, pw, 26, gfx.blend(PANEL, col, 0.2))
            f.sm.text(fb, word, tx + 10, y + 122, col, gfx.blend(PANEL, col, 0.2))

    @staticmethod
    def _tank_state(key, pct):
        """A word for a tank's level, and its colour: fresh water matters when
        it runs low, grey water when it fills up."""
        if key == "fresh":
            if pct < 15:
                return "Refill now", RED
            if pct < 35:
                return "Getting low", AMBER
            return "Plenty", GREEN
        if pct > 85:
            return "Empty now", RED
        if pct > 65:
            return "Empty soon", AMBER
        return "Fine", GREEN

    def _tank(self, x, y, w, h, pct, key):
        """A tank drawn as a cutaway: its shell, the water inside up to pct,
        a filler cap on top, and quarter marks down the side."""
        a, fb = self.aa, self.fb
        water = gfx.rgb(70, 150, 230) if key == "fresh" else gfx.rgb(128, 120, 104)
        top = gfx.blend(water, TXT, 0.35)
        cap_w = 28
        rrect(fb, x + w // 2 - cap_w // 2, y, cap_w, 12, 4, LINE)           # filler cap
        ty, th = y + 9, h - 9
        if a:
            a.rrect(x, ty, w, th, 16, LINE)
            a.rrect(x + 3, ty + 3, w - 6, th - 6, 13, PANEL2)
        else:
            rrect(fb, x, ty, w, th, 16, LINE)
            rrect(fb, x + 3, ty + 3, w - 6, th - 6, 13, PANEL2)
        if pct is None:
            return
        pct = 0 if pct < 0 else 100 if pct > 100 else pct
        ix, iw, ib = x + 3, w - 6, ty + th - 3                   # inside: left, width, bottom
        ih = th - 6
        lh = int(ih * pct / 100)
        if lh > 0:
            ly = ib - lh
            if a and lh > 26:
                a.rrect(ix, ly, iw, lh, 13, water)
                fb.fill_rect(ix, ly, iw, 13, water)             # a flat surface, not rounded
            else:
                fb.fill_rect(ix + 6, ly, iw - 12, lh, water)
            fb.fill_rect(ix + 4, ly, iw - 8, 3, top)              # the surface catching the light
        for q in (1, 2, 3):                                        # quarter marks
            my = ib - ih * q // 4
            fb.hline(x + w - 14, my, 10, gfx.blend(PANEL2, TXT, 0.5))

    def _page_switches(self):
        fb, f, a = self.fb, self.f, self.aa
        self._title = "Switches"
        if not (self.data or {}).get("relays"):
            self._chip = ("Not wired yet", "switch", ("sw", None))
        gap = 8
        cw = (W - 16 - 2 * gap) // 3
        ch = (TAB_Y - BODY_Y - 16 - gap) // 2
        names = self._switch_names()
        icons = self._switch_icons()
        for i in range(len(self.SWITCHES)):
            name, ic = names[i], icons[i]
            on = self.switches[i]
            x = 8 + (i % 3) * (cw + gap)
            y = BODY_Y + 8 + (i // 3) * (ch + gap)
            back = gfx.blend(PANEL, BRAND, 0.22) if on else PANEL
            if a:
                a.rrect(x, y + 3, cw, ch, 14, gfx.BLACK, alpha=110, clip_top=y + ch - 2)
            rrect(fb, x, y, cw, ch, 14, BRAND if on else CARD_HI)
            rrect(fb, x + 1, y + 1, cw - 2, ch - 2, 13, back)
            self._icon(ic, x + 14, y + 14, BRAND if on else MUTED, 26, bg=back)
            font = self._fit(name, cw - 28, (f.mdb, f.sm))
            font.text(fb, name, x + 14, y + 52, TXT if on else MUTED, back)
            # the switch itself, bottom right
            tx, ty = x + cw - 62, y + ch - 36
            rrect(fb, tx, ty, 48, 24, 12, BRAND if on else PANEL2)
            kx = tx + 36 if on else tx + 12
            if a:
                a.disc(kx, ty + 12, 9, TXT if on else MUTED)
            f.sm.text(fb, "On" if on else "Off", x + 14, y + ch - 32,
                      BRAND if on else MUTED, back)
            self._hit(x, y, cw, ch, ("sw", i))

    def _page_heater(self):
        fb, f, a = self.fb, self.f, self.aa
        h = self._heater()
        have = h.get("connected") or h.get("remembered")
        on = bool(have and h.get("on"))
        target = self._target()
        cx, cy, R, th = self.DIAL
        r0 = R - th
        start, end = self._t_to_angle(self.T_MIN), self._t_to_angle(self.T_MAX)
        ta = self._t_to_angle(target)
        self._chip = [("Dial", "level", ("hview", "dial")) if self.heater_view == "flow"
                      else ("Diagram", "flame", ("hview", "flow")),
                      ("Fan", "fan", ("sub", "fan")),
                      ("Timers", "clock", ("sub", "schedule"))]
        if self.heater_view == "flow":
            self._heater_flow(h, have)
            self._heater_controls(h, on)
            return

        # the ring: the track, a glow while it runs, then the warm gradient
        a.arc(cx, cy, r0, R, start, end, PANEL2)
        if on:
            a.arc(cx, cy, r0 - 6, R + 6, start, ta, FLAME, alpha=40)
        hot0 = AMBER if on else gfx.blend(PANEL2, AMBER, 0.55)
        hot1 = EMBER if on else gfx.blend(PANEL2, EMBER, 0.55)
        n = self.GRAD_STEPS
        for i in range(n):
            s0 = start + (ta - start) * i / n
            s1 = min(ta, start + (ta - start) * (i + 1.1) / n)
            if s1 > s0:
                a.arc(cx, cy, r0, R, s0, s1, gfx.blend(hot0, hot1, i / (n - 1)), caps=(i == 0))
        # the knob at the target
        rm = r0 + th / 2
        kx, ky = cx + math.sin(ta) * rm, cy - math.cos(ta) * rm
        a.disc(kx + 1, ky + 3, 14, gfx.BLACK, alpha=120)
        a.disc(kx, ky, 14, TXT)
        a.disc(kx, ky, 8, hot1)
        # where the cabin actually is, as a mark just inside the ring
        cab = h.get("air_temp_c") if have else None
        if cab is not None:
            self._radial(cx, cy, self._t_to_angle(cab), r0 - 14, r0 - 5, 3, TXT)

        # the middle: what it is set to, and what it is doing
        f.sm.text(fb, "HEATING TO" if on else "SET TO", cx, cy - 62, MUTED, self.bgc(cy - 55), 1)
        tw = f.xxl.width("%d" % target)
        f.xxl.text(fb, "%d" % target, cx - 8, cy - 44, TXT, self.bgc(cy - 10), 1)
        f.lg.text(fb, "°", cx - 8 + tw // 2 + 2, cy - 42, MUTED, self.bgc(cy - 30))
        if not have:
            state, scol, ic = "Not read yet", MUTED, None
        elif h.get("fault"):
            state, scol, ic = "Fault E%s" % h.get("error_code", "?"), RED, None
        elif on:
            state, scol, ic = "Heating · " + (h.get("mode") or "on"), FLAME, "flame"
        else:
            state, scol, ic = "Off", MUTED, None
        sw = f.sm.width(state) + (22 if ic else 0) + 24
        pb = gfx.blend(self.bgc(cy + 45), scol, 0.18)
        self._pill(cx - sw // 2, cy + 34, sw, 26, pb)
        tx = cx - sw // 2 + 12
        if ic:
            self._icon(ic, tx, cy + 39, scol, 16, bg=pb)
            tx += 22
        f.sm.text(fb, state, tx, cy + 38, scol, pb)
        if cab is not None:
            f.sm.text(fb, "Cabin %d°" % round(cab), cx, cy + 66, MUTED, self.bgc(cy + 70), 1)

        en = not self.busy
        # round - and + at the foot of the dial; the ring itself takes a tap
        if en:
            self._hit(cx - R, cy - R, 2 * R, 2 * R - 40, ("dial", 0))
        self._round_button(cx - R + 6, cy + R - 10, 22, "minus", ("temp", -1), en)
        self._round_button(cx + R - 6, cy + R - 10, 22, "plus", ("temp", 1), en)

        # the last command's outcome, between - and +
        msg, mcol = None, MUTED
        if self.busy:
            msg, mcol = self.busy, BLUE
        elif self.result:
            msg, mcol = self.result
        elif have and h.get("at"):
            hm = self.clock(h["at"])
            if hm:
                msg = "Read at %02d:%02d" % hm
        if msg:
            while f.sm.width(msg) > 2 * R - 80:           # between - and +
                msg = msg[:-2] + "…"
            f.sm.text(fb, msg, cx, TAB_Y - 24, mcol, self.bgc(TAB_Y - 16), 1)

        self._heater_controls(h, on)

    def _heater_controls(self, h, on):
        a = self.aa
        en = not self.busy
        # the controls down the right
        x, w, bh, gap = 300, 170, 40, 7
        rows = [BODY_Y + 10 + i * (bh + gap) for i in range(5)]
        running = self._mode(h)
        for i, (act, label, ic) in enumerate((("air", "Air heat", "flame"),
                                              ("combi", "Air + water", "flame"),
                                              ("water", "Water heat", "drop"))):
            lit = running == act
            if lit:
                a.rrect(x, rows[i] + 3, w, bh, 14, gfx.BLACK, alpha=110, clip_top=rows[i] + bh - 2)
            self._button(x, rows[i], w, bh, label, ("heat", act), icon=ic,
                         fg=gfx.BLACK if lit else TXT,
                         back=FLAME if lit else PANEL2, enabled=en)
        self._button(x, rows[3], w - 52, bh, WATER[self.water], ("water", 0), icon="drop",
                     enabled=en)
        self._button(x + w - 44, rows[3], 44, bh, "", ("heat", "read"), icon="refresh",
                     enabled=en)
        self._button(x, rows[4], w, bh, "Off", ("heat", "off"), icon="power",
                     back=gfx.blend(PANEL2, RED, 0.5 if on else 0.3), enabled=en)

    # -- drawing helpers ---------------------------------------------------------

    def _radial(self, cx, cy, a, r0, r1, w, col):
        if self.aa:
            s_, c_ = math.sin(a), math.cos(a)
            self.aa.line(cx + s_ * r0, cy - c_ * r0, cx + s_ * r1, cy - c_ * r1, w, col)
            return
        self._radial_hard(cx, cy, a, r0, r1, w, col)

    def _radial_hard(self, cx, cy, a, r0, r1, w, col):
        """A straight bar from radius r0 to r1 at angle a (0 = 12 o'clock,
        clockwise), w pixels wide. Clock ticks and hands, and the sun's rays."""
        s, c = math.sin(a), math.cos(a)
        hw = w / 2
        self.fb.poly(cx, cy, array("h", (
            int(s * r0 + c * hw), int(-c * r0 + s * hw),
            int(s * r1 + c * hw), int(-c * r1 + s * hw),
            int(s * r1 - c * hw), int(-c * r1 - s * hw),
            int(s * r0 - c * hw), int(-c * r0 - s * hw))), col, True)

    def _bolt(self, x, y, size, col):
        if self.aa:
            icons.draw(self.aa, "bolt", x, y, col, size)
            return
        self._bolt_hard(x, y, size, col)

    def _bolt_hard(self, x, y, size, col):
        pts = (0.55, 0, 0.05, 0.55, 0.42, 0.55, 0.25, 1.0, 0.95, 0.4, 0.58, 0.4, 0.8, 0)
        self.fb.poly(x, y, array("h", [int(v * size) for v in pts]), col, True)

    def _batt_state(self, d):
        """(state, colour, time line) for the battery, as the Power page words it."""
        cur = d.get("current") or 0.0
        if cur > 0.05:
            cap = (d.get("nominal_ah") or 0) - (d.get("residual_ah") or 0)
            return "Charging", GREEN, ("Full in " + _hm(cap / cur) if cap > 0 else "Full")
        if cur < -0.05:
            return "Discharging", AMBER, _hm((d.get("residual_ah") or 0) / -cur) + " left"
        return "Resting", MUTED, "No current flowing"

    # -- Home ------------------------------------------------------------------

    def _page_home(self):
        fb, f = self.fb, self.f
        if getattr(self, "paused_game", None):
            self._chip = ("Back to the game", "back", ("resume", 0))
        p = self.parts()
        self._clock_face(124, 157, 108, p)
        x, w = 248, 224
        y, h = BODY_Y + 8, 128
        self._weather_card(x, y, w, h, p)
        y2 = y + h + 8
        self._battery_card(x, y2, w, TAB_Y - 8 - y2)

    FACE_KEY = gfx.rgb(0, 0, 8)          # "transparent" in the cached face

    def _face(self, R):
        """The clock face - rim, 60 ticks, numerals - drawn once into its own
        image and reused. Drawing the ticks smooth takes ~60 ms; blitting the
        finished face takes about one. Redrawn only if the size or the
        palette (night mode) changes."""
        key = (R, self.night)
        if getattr(self, "_face_cache", None) and self._face_cache[0] == key:
            return self._face_cache[1:]
        size = 2 * R + 16
        buf = bytearray(size * size * 2)
        img = framebuf.FrameBuffer(buf, size, size, framebuf.RGB565)
        img.fill(self.FACE_KEY)
        a = AA(buf, size, size, img)
        c = size / 2
        a.disc(c, c + 3, R + 4, gfx.BLACK, alpha=110)              # its shadow
        a.disc(c, c, R + 4, CARD_HI)
        a.disc(c, c, R + 2, gfx.blend(PANEL, BG_BOT, 0.35))
        a.disc(c, c, R - 6, PANEL)
        for i in range(60):
            t = i * math.pi / 30
            big = i % 5 == 0
            r0 = R - (15 if big else 9)
            a.line(c + math.sin(t) * r0, c - math.cos(t) * r0,
                   c + math.sin(t) * (R - 5), c - math.cos(t) * (R - 5),
                   2.6 if big else 1.2, TXT if big else gfx.blend(PANEL, MUTED, 0.7))
        for n, t in ((12, 0.0), (3, math.pi / 2), (6, math.pi), (9, 1.5 * math.pi)):
            self.f.md.text(img, str(n), int(c + math.sin(t) * (R - 32)),
                           int(c - math.cos(t) * (R - 32)) - self.f.md.height // 2, MUTED, PANEL, 1)
        self._face_cache = (key, img, size)
        return img, size

    def _clock_face(self, cx, cy, R, p):
        fb, f, a = self.fb, self.f, self.aa
        if a is None:
            return
        img, size = self._face(R)
        fb.blit(img, cx - size // 2, cy - size // 2, self.FACE_KEY)
        if p:
            hh, mm, ss = p[3], p[4], p[5]
            for ang, ln, wd in (((hh % 12 + mm / 60) * math.pi / 6, R * 0.52, 7),
                                ((mm + ss / 60) * math.pi / 30, R * 0.78, 5)):
                s_, c_ = math.sin(ang), math.cos(ang)
                a.line(cx + 2, cy + 3, cx + 2 + s_ * ln, cy + 3 - c_ * ln, wd, gfx.BLACK, alpha=90)
                a.line(cx - s_ * 12, cy + c_ * 12, cx + s_ * ln, cy - c_ * ln, wd, TXT)
            if self.seconds:
                sa = ss * math.pi / 30
                a.line(cx - math.sin(sa) * 18, cy + math.cos(sa) * 18,
                       cx + math.sin(sa) * R * 0.86, cy - math.cos(sa) * R * 0.86, 2, BRAND)
        else:
            f.sm.text(fb, "waiting for the hub", cx, cy + R // 3, MUTED, PANEL, 1)
        a.disc(cx, cy, 6, BRAND)
        a.disc(cx, cy, 2.5, PANEL)
        # tapping the clock face opens the kitchen timer
        self._hit(cx - R, cy - R, 2 * R, 2 * R, ("sub", "timer"))

    def _weather_card(self, x, y, w, h, p):
        fb, f = self.fb, self.f
        self._card(x, y, w, h, "Weather")
        self._hit(x, y, w, h, ("sub", "forecast"))
        wx = self.weather or {}
        meta = wx.get("meta") or {}
        data = wx.get("data") or {}
        cur = data.get("current") or {}
        place = meta.get("place") or ""
        while place and f.sm.width(place) > 120:
            place = place[:-2] + "…"
        if place:
            f.sm.text(fb, place, x + w - 12, y + 10, MUTED, PANEL, 2)
        if cur.get("temperature_2m") is None:
            f.md.text(fb, "No forecast yet", x + 12, y + 40, MUTED, PANEL)
            for i, ln in enumerate(self._wrap(f.sm, "The hub fetches it when it is online",
                                              w - 24)[:3]):
                f.sm.text(fb, ln, x + 12, y + 68 + i * 18, MUTED, PANEL)
            return
        code = cur.get("weather_code")
        # Rough day and night from the clock: the forecast here carries no
        # sunrise, and a moon at 7 pm in June is a small price.
        night = p is not None and (p[3] < 7 or p[3] >= 20)
        self._wx_icon(x + 46, y + 68, code, night)
        f.xl.text(fb, "%d°" % round(cur["temperature_2m"]), x + 92, y + 30, TXT, PANEL)
        cond = WMO.get(code, DASH)
        self._fit(cond, w - 104, (f.md, f.sm)).text(fb, cond, x + 92, y + 78, TXT, PANEL)
        age = meta.get("age_s") or 0
        if meta.get("stale") or age > 3 * 3600:
            f.sm.text(fb, "Forecast " + _hm(age / 3600) + " old", x + 12, y + 104,
                      AMBER, PANEL)
            return
        d = data.get("daily") or {}
        hi = (d.get("temperature_2m_max") or [None])[0]
        lo = (d.get("temperature_2m_min") or [None])[0]
        line = "Hi %s° Lo %s°" % (_num(hi, "%d"), _num(lo, "%d"))
        if cur.get("wind_speed_10m") is not None:
            windy = line + " · %d km/h" % round(cur["wind_speed_10m"])
            if f.sm.width(windy) <= w - 24:      # wind only if it fits
                line = windy
        f.sm.text(fb, line, x + 12, y + 104, MUTED, PANEL)

    def _wx_icon(self, cx, cy, code, night):
        """A weather symbol about 60 px across, centred on (cx, cy), drawn on
        the card colour."""
        fb = self.fb
        c = code if code is not None else -1

        def sun(x, y, r):
            if night:
                self._disc(x, y, r, MOON)
                o = int(r * 0.8)
                self._disc(x + int(r * 0.45), y - int(r * 0.35), o, PANEL)
                return
            for i in range(8):
                self._radial(x, y, i * math.pi / 4, r + 4, r + 10, 3, SUN)
            self._disc(x, y, r, SUN)

        def cloud(x, y, w, col):
            a, b, d = int(0.22 * w), int(0.30 * w), int(0.20 * w)
            self._disc(x - int(0.24 * w), y + int(0.06 * w), a, col)
            self._disc(x + int(0.02 * w), y - int(0.08 * w), b, col)
            self._disc(x + int(0.28 * w), y + int(0.08 * w), d, col)
            fb.fill_rect(x - int(0.24 * w), y + int(0.06 * w), int(0.52 * w), a + 1, col)

        if c == 0:
            sun(cx, cy, 16)
        elif c in (1, 2):
            sun(cx - 10, cy - 12, 12)
            cloud(cx + 6, cy + 6, 44 if c == 1 else 54, CLOUD)
        elif c == 3:
            cloud(cx, cy, 62, CLOUD)
        elif c in (45, 48):
            cloud(cx, cy - 10, 52, CLOUD)
            for i in range(3):
                fb.fill_rect(cx - 22 + (i % 2) * 6, cy + 12 + i * 6, 40, 2, MUTED)
        elif c >= 51:
            storm = c >= 95
            cloud(cx, cy - 10, 58, STORM if storm else CLOUD)
            y0 = cy + 12
            if storm:
                self._bolt(cx - 9, y0 - 2, 20, SUN)
            elif c in (71, 73, 75, 77, 85, 86):
                for i, dx in enumerate((-16, -2, 12)):
                    self._disc(cx + dx, y0 + 5 + (i % 2) * 6, 3, TXT)
            else:
                col = CYAN if c in (56, 57, 66, 67) else BLUE
                drops = (-16, -4, 8, 20) if c in (55, 65, 67, 81, 82) else (-14, 0, 14)
                ln = 5 if c in (51, 53, 55, 56, 57) else 11
                for dx in drops:
                    gfx.thick_line(fb, cx + dx, y0, cx + dx - 4, y0 + ln, col, 2)
        else:
            self.f.lg.text(fb, "?", cx, cy - 15, MUTED, PANEL, 1)

    def _battery_card(self, x, y, w, h):
        fb, f = self.fb, self.f
        self._card(x, y, w, h)
        d = self.data or {}
        ok = d.get("connected")
        soc = d.get("soc") if ok else None
        charging = ok and (d.get("current") or 0) > 0.05
        col = GREEN if (soc or 0) > 50 else AMBER if (soc or 0) > 20 else RED
        # the battery itself, filled to its charge, with a bolt while charging
        bx, by, bw, bh = x + 12, y + 14, 96, 46
        rrect(fb, bx + bw, by + bh // 2 - 9, 6, 18, 2, MUTED)
        rrect(fb, bx, by, bw, bh, 8, MUTED)
        rrect(fb, bx + 3, by + 3, bw - 6, bh - 6, 6, PANEL)
        if soc is not None:
            fw = int((bw - 12) * max(0, min(100, soc)) / 100)
            if fw > 0:
                rrect(fb, bx + 6, by + 6, fw, bh - 12, 4 if fw >= 8 else 0, col)
        if charging:
            self._bolt(bx + bw // 2 - 12, by + 7, bh - 14, TXT)
        if not ok:
            f.mdb.text(fb, "Offline", x + 124, y + 26, MUTED, PANEL)
            f.sm.text(fb, "Waiting for the BMS", x + 12, y + 66, MUTED, PANEL)
            return
        f.lg.text(fb, _num(soc, "%d") + "%", x + 124, y + 12, TXT, PANEL)
        state, scol, eta = self._batt_state(d)
        f.sm.text(fb, state, x + 124, y + 48, scol, PANEL)
        v = _num(d.get("voltage"), "%.2f")
        f.sm.text(fb, _fits(f.sm, w - 24, ("%s · %s V" % (eta, v),
                                          "%s · %s V" % (eta.replace(" h ", "h ").replace(" m", "m"), v),
                                          eta)), x + 12, y + 66, MUTED, PANEL)

    # -- alarm -------------------------------------------------------------------

    def _wrap(self, font, text, width):
        lines, line = [], ""
        for word in text.split():
            t = (line + " " + word) if line else word
            if font.width(t) <= width or not line:
                line = t
            else:
                lines.append(line)
                line = word
        if line:
            lines.append(line)
        return lines

    def _alarm(self, a):
        fb, f = self.fb, self.f
        wash = gfx.blend(BG, RED, 0.25)
        fb.fill_rect(0, 0, W, H, wash)
        x, y, w, h = 30, 30, 420, 260
        rrect(fb, x - 3, y - 3, w + 6, h + 6, 16, RED)
        self._panel(x, y, w, h)
        f.lg.text(fb, a.get("title", "Alert"), x + w // 2, y + 20, RED, PANEL, 1)
        ty = y + 66
        for ln in self._wrap(f.md, a.get("detail", ""), w - 40)[:4]:
            f.md.text(fb, ln, x + w // 2, ty, TXT, PANEL, 1)
            ty += 26
        self._button(x + 60, y + h - 74, w - 120, 58, "Clear alarm", ("ack", a.get("id")),
                     fg=TXT, back=RED)

    # -- brightness --------------------------------------------------------------

    def _brightness(self):
        fb, f = self.fb, self.f
        fb.fill_rect(0, 0, W, H, gfx.blend(BG, gfx.BLACK, 0.5))
        x, y, w, h = 12, 28, 456, 264
        self._panel(x, y, w, h)
        f.lg.text(fb, "Display", x + w // 2, y + 16, TXT, PANEL, 1)
        b = self.dbatt
        if b:
            f.sm.text(fb, "Battery %d%%" % b["pct"], x + 16, y + 12, TXT, PANEL)
            st = {"charging": "Charging", "full": "Full"}.get(b["state"], "%.2f V" % b["v"])
            f.sm.text(fb, st, x + w - 16, y + 12, GREEN if b["state"] != "battery" else MUTED, PANEL, 2)
        ns = self.night_src
        if self.night_mode == "auto" and ns:
            if ns[0] is None:
                txt = "Night 20:00 to 07:00 (no location)"
            else:
                txt = "Night %s to %s \u00b7 %s" % (self.hm(ns[0]), self.hm(ns[1]), ns[2])
            f.sm.text(fb, txt, x + w // 2, y + 124, MUTED, PANEL, 1)
        self._button(x + 16, y + 62, 64, 56, "", ("bright", -1), icon="minus")
        self._button(x + w - 80, y + 62, 64, 56, "", ("bright", 1), icon="plus")
        self._slider("bright", x + 100, y + 78, w - 200, 14,
                     (self.brightness - 0.1) / 0.9, BRAND)
        f.md.text(fb, "%d%%" % round(self.brightness * 100), x + w // 2, y + 98,
                  MUTED, PANEL, 1)
        tw = (w - 48) // 3
        self._button(x + 16, y + h - 106, tw, 44,
                     "Sound on" if self.sound_on else "Sound off", ("sound", 0),
                     fg=TXT if self.sound_on else MUTED)
        self._button(x + 24 + tw, y + h - 106, tw, 44,
                     "Night " + self.night_mode, ("night", 0))
        self._button(x + 32 + 2 * tw, y + h - 106, tw, 44,
                     "Dim on" if self.dim_on else "Dim off", ("dim", 0),
                     fg=TXT if self.dim_on else MUTED)
        t4 = (w - 56) // 4
        self._button(x + 16, y + h - 56, t4, 44,
                     "Keep it on" if self.offmode else "Screen off", ("screen_off", 0),
                     fg=gfx.BLACK if self.offmode else TXT,
                     back=BRAND if self.offmode else PANEL2)
        # a fresh start, for when something is stuck - no opening the case
        self._button(x + 24 + t4, y + h - 56, t4, 44, "Restart", ("restart", 0))
        # off altogether, until it is plugged in to charge (shutdown.py)
        self._button(x + 32 + 2 * t4, y + h - 56, t4, 44, "Shut down", ("shutdown", 0),
                     fg=RED)
        self._button(x + 40 + 3 * t4, y + h - 56, t4, 44, "Done", ("bright", "done"))

    # -- Level -----------------------------------------------------------------

    @staticmethod
    def _van_extent(shapes):
        """The box a van sweeps through over the whole tilt range, relative to
        its pivot, in drawing units. Worked out once per drawing, so a van
        placed to fit this box stays inside it at any angle."""
        pts = []
        for _, sh in shapes:
            if sh[0] == "c":
                _, cx, cy, r = sh
                pts += [(cx - r, cy - r), (cx + r, cy - r), (cx - r, cy + r), (cx + r, cy + r)]
            elif sh[0] == "r":
                _, x, y, w, h = sh
                pts += [(x, y), (x + w, y), (x, y + h), (x + w, y + h)]
            else:
                p = sh[1]
                pts += [(p[i], p[i + 1]) for i in range(0, len(p), 2)]
        lo_x = lo_y = 1e9
        hi_x = hi_y = -1e9
        for deg in range(-VAN_MAX_DEG, VAN_MAX_DEG + 1, 5):
            a = math.radians(deg)
            ca, sa = math.cos(a), math.sin(a)
            for x, y in pts:
                dx, dy = x - 150, y - 140
                rx, ry = dx * ca - dy * sa, dx * sa + dy * ca
                lo_x, hi_x = min(lo_x, rx), max(hi_x, rx)
                lo_y, hi_y = min(lo_y, ry), max(hi_y, ry)
        return lo_x, lo_y, hi_x, hi_y

    def _van_fit(self, shapes, x, y, w, h):
        """Scale and pivot position that keep a van inside (x, y, w, h) at
        every tilt the page can show."""
        cache = self._vanfit
        key = (id(shapes), x, y, w, h)
        if key not in cache:
            lo_x, lo_y, hi_x, hi_y = self._van_extent(shapes)
            sc = min(w / (hi_x - lo_x), h / (hi_y - lo_y))
            px = x + (w - (hi_x - lo_x) * sc) / 2 - lo_x * sc
            py = y + (h - (hi_y - lo_y) * sc) / 2 - lo_y * sc
            cache[key] = (sc, px, py)
        return cache[key]

    def _van(self, shapes, sc, px, py, deg):
        """Draw one of the web page's vans at scale sc, its pivot (the middle
        of the ground line) at (px, py), turned clockwise by deg (exaggerated
        already, by van_tilt(), as on the web)."""
        fb = self.fb
        a = math.radians(deg)
        ca, sa = math.cos(a), math.sin(a)

        def tr(x, y):
            dx, dy = (x - 150) * sc, (y - 140) * sc
            return int(px + dx * ca - dy * sa), int(py + dx * sa + dy * ca)

        g = globals()
        for col, sh in shapes:
            col = g[col]
            if sh[0] == "c":
                cx, cy = tr(sh[1], sh[2])
                r = int(sh[3] * sc)
                self._disc(cx, cy, r, col)
                continue
            if sh[0] == "r":
                _, x, y, w, h = sh
                pts = (x, y, x + w, y, x + w, y + h, x, y + h)
            else:
                pts = sh[1]
            if self.aa:
                self.aa.poly([tr(pts[i], pts[i + 1]) for i in range(0, len(pts), 2)], col)
                continue
            arr = array("h")
            for i in range(0, len(pts), 2):
                X, Y = tr(pts[i], pts[i + 1])
                arr.append(X)
                arr.append(Y)
            fb.poly(0, 0, arr, col, True)

    def _vial_img(self, R, ok_deg, warn_deg, max_deg):
        """The vial without its bubble - rim, rings, cross and labels - drawn
        once and copied, as the clock face is."""
        key = (R, ok_deg, warn_deg, max_deg, self.night)
        c = getattr(self, "_vial_cache", None)
        if c and c[0] == key:
            return c[1], c[2]
        size = 2 * R + 8
        buf = bytearray(size * size * 2)
        img = framebuf.FrameBuffer(buf, size, size, framebuf.RGB565)
        img.fill(self.FACE_KEY)
        a, f, m = AA(buf, size, size, img), self.f, size // 2
        a.disc(m, m, R + 2, LINE)
        a.disc(m, m, R, PANEL2)
        for deg in (ok_deg, warn_deg):
            rr = int(radius_for(deg, R - 14, max_deg))
            if rr > 2:
                a.ring(m, m, rr - 0.6, rr + 0.6, LINE)
        img.hline(m - R + 6, m, 2 * R - 12, LINE)
        img.vline(m, m - R + 6, 2 * R - 12, LINE)
        f.sm.text(img, "NOSE UP", m, m - R + 10, MUTED, PANEL2, 1)
        f.sm.text(img, "NOSE DOWN", m, m + R - 26, MUTED, PANEL2, 1)
        f.sm.text(img, "L", m - R + 10, m - 18, MUTED, PANEL2)
        f.sm.text(img, "R", m + R - 18, m - 18, MUTED, PANEL2)
        self._vial_cache = (key, img, size)
        return img, size

    def _vial(self, roll, pitch, qr, qp, off, ok_deg, warn_deg, max_deg, colour, l):
        fb, f = self.fb, self.f
        cx, cy, R = 104, BODY_Y + 8 + 88, 86
        img, size = self._vial_img(R, ok_deg, warn_deg, max_deg)
        fb.blit(img, cx - size // 2, cy - size // 2, self.FACE_KEY)
        if l.get("cal_stale") or not l.get("calibrated"):
            f.sm.text(fb, "NOT CALIBRATED", cx, cy + R // 2 - 4, AMBER, PANEL2, 1)
        # Placed from whole degrees, like the web page, so it settles instead of
        # shimmering; floats to the HIGH side, like a real bubble.
        rr = radius_for(math.sqrt(qr * qr + qp * qp), R - 14, max_deg)
        a = math.atan2(qp, qr)
        bx, by = int(cx + rr * math.cos(a)), int(cy - rr * math.sin(a))
        col = colour(off)
        self._disc(bx, by, 14, gfx.blend(col, TXT, 0.3))
        self._disc(bx, by, 12, col)

    def wheel_raise(self, roll, pitch):
        """Centimetres to raise each wheel (FL, FR, RL, RR) to level the van.

        Each wheel's height relative to the middle of the van is its sideways
        offset times tan(roll) plus its fore-and-aft offset times tan(pitch)
        (roll positive = right side high, pitch positive = nose high, as the
        hub reports them). The highest wheel stays put and the others come up
        to it, because ramps can only raise a wheel."""
        wb, tr = self.van
        tr_r, tp = math.tan(math.radians(roll)), math.tan(math.radians(pitch))
        wheels = ((-tr / 2, wb / 2), (tr / 2, wb / 2), (-tr / 2, -wb / 2), (tr / 2, -wb / 2))
        h = [x * tr_r + y * tp for x, y in wheels]
        top = max(h)
        return [(top - v) / 10 for v in h]       # mm to cm

    def _wheels(self, roll, pitch, l):
        fb, f = self.fb, self.f
        x0, y0, w, h = 8, BODY_Y + 8, 198, TAB_Y - 8 - 36 - 8 - (BODY_Y + 8)
        self._card(x0, y0, w, h, "Raise each wheel")
        cm = self.wheel_raise(roll, pitch)
        # the van from above, nose at the top
        vw = 50                                  # narrow, so "9.5 cm" fits either side
        vx, vy, vh = x0 + (w - vw) // 2, y0 + 32, h - 58   # clear of the note below
        rrect(fb, vx, vy, vw, vh, 12, VAN_BODY)
        fb.fill_rect(vx + 8, vy + 14, vw - 16, 14, VAN_GLASS)       # windscreen
        # an arrow to the front, under the windscreen ("FRONT" will not fit)
        f.lg.text(fb, "↑", vx + vw // 2, vy + 34, PANEL, VAN_BODY, 1)
        # wheels, and the height beside each
        pos = ((vx - 7, vy + 20, 2), (vx + vw - 1, vy + 20, 0),
               (vx - 7, vy + vh - 44, 2), (vx + vw - 1, vy + vh - 44, 0))
        for (wx, wy, align), v in zip(pos, cm):
            fb.fill_rect(wx - 1, wy, 10, 26, VAN_WHEEL)
            # half-centimetre steps; whole centimetres from 10 up, where a half
            # no longer matters and "12.5 cm" would not fit beside the wheel
            v = round(v) if v >= 10 else round(v * 2) / 2
            col = GREEN if v <= 0.5 else TXT
            txt = "0" if v <= 0 else ("%.1f" % v).replace(".0", "")
            # the figure, then its unit - "cm" on every one, so nobody reads mm
            uw = f.sm.width(" cm")
            if align == 2:
                tx = wx - 5
                f.sm.text(fb, " cm", tx, wy + 5, MUTED, PANEL, 2)
                f.mdb.text(fb, txt, tx - uw, wy + 2, col, PANEL, 2)
            else:
                tx = wx + 12
                n = f.mdb.text(fb, txt, tx, wy + 2, col, PANEL)
                f.sm.text(fb, " cm", tx + n, wy + 5, MUTED, PANEL)
        worst = max(cm)
        if l.get("cal_stale") or not l.get("calibrated"):
            f.sm.text(fb, "Not calibrated", x0 + w // 2, y0 + h - 20, AMBER, PANEL, 1)
        elif worst > 12:
            f.sm.text(fb, "Too steep for ramps", x0 + w // 2, y0 + h - 20, AMBER, PANEL, 1)
        else:
            f.sm.text(fb, "to level the van", x0 + w // 2, y0 + h - 20, MUTED, PANEL, 1)

    def _page_level(self):
        fb, f = self.fb, self.f
        d = self.data or {}
        l = self.level or d.get("level")
        if not l or not l.get("enabled", True):
            self._placeholder("Levelling is off", "Enable it in the hub settings")
            return
        if not l.get("ok") and not l.get("present", True):
            self._placeholder("No level sensor", "The hub cannot see the sensor")
            return
        self._chip = ("G-force", "engine", ("sub", "gforce"))
        sc = d.get("level_scale") or {}
        ok_deg, warn_deg, max_deg = sc.get("ok", 1.0), sc.get("warn", 20.0), sc.get("max", 30.0)
        roll, pitch = l.get("roll") or 0.0, l.get("pitch") or 0.0
        off = l.get("off_by")
        if off is None:
            off = math.sqrt(roll * roll + pitch * pitch)

        def colour(v):
            v = abs(v)
            return GREEN if v <= ok_deg else RED if v >= warn_deg else AMBER

        self._title = "Level" if off <= ok_deg else "Level · %.1f° out" % off
        qr, qp = round(roll), round(pitch)

        # left: the bubble or the wheel heights, with a switch underneath
        if self.level_view == "wheels":
            self._wheels(roll, pitch, l)
        else:
            self._vial(roll, pitch, qr, qp, off, ok_deg, warn_deg, max_deg, colour, l)
        by, bw = TAB_Y - 8 - 36, 96
        for i, (key, label) in enumerate((("bubble", "Bubble"), ("wheels", "Wheels"))):
            on = self.level_view == key
            self._button(8 + i * (bw + 6), by, bw, 36, label, ("lview", key),
                         fg=BRAND if on else MUTED, back=PANEL2 if on else PANEL)

        # the two vans, as on the web Level page
        x, w, bh = 212, 260, 108
        views = (
            # front view: facing the van, so its right is on our left; a
            # positive roll (right side high) turns the drawing clockwise
            ("FROM THE FRONT", VAN_FRONT, roll, qr, "raise the", ("LEFT", "RIGHT"), True),
            # side view, nose to the right: nose-high turns it anticlockwise
            ("FROM THE SIDE", VAN_SIDE, -pitch, qp, "raise the", ("REAR", "FRONT"), False),
        )
        for i, (label, shapes, turn, q, lead, sides, front) in enumerate(views):
            y = BODY_Y + 8 + i * (bh + 8)
            self._card(x, y, w, bh)
            # the drawing gets the left of the card below the label, less a
            # strip for RIGHT / LEFT under the front view
            ax, ay, aw, ah = x + 6, y + 26, 146, bh - 30 - (14 if front else 0)
            sc, px, py = self._van_fit(shapes, ax, ay, aw, ah)
            fb.hline(ax, int(py), aw, LINE)
            self._van(shapes, sc, px, py, van_tilt(turn, ok_deg))
            f.sm.text(fb, label, x + 12, y + 8, MUTED, PANEL)
            if front:
                f.sm.text(fb, "RIGHT", ax, y + bh - 20, MUTED, PANEL)
                f.sm.text(fb, "LEFT", ax + aw, y + bh - 20, MUTED, PANEL, 2)
            tx = x + 160
            f.lg.text(fb, "%d°" % abs(q), tx, y + 24, colour(turn), PANEL)
            if abs(turn) <= ok_deg:                # within the tolerance: nothing to do
                f.sm.text(fb, "level", tx, y + 62, MUTED, PANEL)
            else:
                f.sm.text(fb, lead, tx, y + 62, MUTED, PANEL)
                f.mdb.text(fb, sides[0] if turn > 0 else sides[1], tx, y + 80, TXT, PANEL)

    # -- Drive -----------------------------------------------------------------

    def engine_running(self):
        """As the hub judges it for the plugged-in alarm: alternator current,
        or the alternator line at 13.8 V or more."""
        d = self.data or {}
        if "engine" in d:                        # the hub's own judgement
            return bool(d["engine"])
        r = d.get("renogy") or {}
        return bool(r.get("connected")) and ((r.get("alt_a") or 0) > 0.05
                                             or (r.get("alt_v") or 0) >= 13.8)

    def drive_checks(self):
        """The checks the hub can answer: (label, state, note), state being
        ok, warn or bad."""
        d = self.data or {}
        out = []
        if d.get("mains_live"):
            out.append(("Hook-up", "bad", "Still plugged in!"))
        else:
            out.append(("Hook-up", "ok", "Unplugged"))
        h = d.get("heater") or {}
        if not (h.get("connected") or h.get("remembered")):
            out.append(("Heater off", "warn", "Not read - check it"))
        elif h.get("on"):
            out.append(("Heater off", "bad", "Heater is on"))
        else:
            out.append(("Heater off", "ok", "Off"))
        a = self.alarm()
        out.append(("No alarms", "bad" if a else "ok", a.get("title", "Alarm") if a else "None"))
        soc = d.get("soc") if d.get("connected") else None
        if soc is None:
            out.append(("Battery", "warn", "Offline"))
        elif soc < 20:
            out.append(("Battery", "warn", "%d%% - low" % soc))
        else:
            out.append(("Battery", "ok", "%d%%" % soc))
        return out

    def items(self):
        """The Drive list: the hub's (set on its Settings page), else this
        display's own config."""
        cl = (self.data or {}).get("checklist")
        return cl if isinstance(cl, list) else self.checklist

    def drive_ready(self):
        return (all(c[1] == "ok" for c in self.drive_checks())
                and all(i in self.ticks for i in self.items()))

    def _page_drive(self):
        fb, f = self.fb, self.f
        checks = self.drive_checks()
        items = self.items()
        left = (sum(1 for c in checks if c[1] != "ok")
                + sum(1 for i in items if i not in self.ticks))
        self._title = "Ready to drive" if left == 0 else "Not ready \u00b7 %d to check" % left
        dots = {"ok": GREEN, "warn": AMBER, "bad": RED}
        x, y, w, h = 8, BODY_Y + 8, 224, TAB_Y - BODY_Y - 16
        self._card(x, y, w, h, "The van")
        for i, (label, state, note) in enumerate(checks):
            ry = y + 32 + i * 48
            self._disc(x + 20, ry + 11, 7, dots[state])
            f.mdb.text(fb, label, x + 36, ry, TXT, PANEL)
            while f.sm.width(note) > w - 48:
                note = note[:-2] + "…"
            f.sm.text(fb, note, x + 36, ry + 22, dots[state] if state != "ok" else MUTED, PANEL)
        x, w = 240, 232
        self._card(x, y, w, h, "Before you go")
        n = len(items)
        rh = min(40, (h - 34) // max(1, n))
        # one size for every item: the largest the longest one fits in
        font = f.md
        for item in items:
            if f.md.width(item) > w - 58:
                font = f.sm
        for i, item in enumerate(items):
            ry = y + 30 + i * rh
            on = item in self.ticks
            bx, by = x + 12, ry + (rh - 22) // 2
            rrect(fb, bx, by, 22, 22, 4, GREEN if on else LINE)
            if not on:
                rrect(fb, bx + 2, by + 2, 18, 18, 3, PANEL)
            else:
                gfx.thick_line(fb, bx + 5, by + 11, bx + 9, by + 16, BG, 3)
                gfx.thick_line(fb, bx + 9, by + 16, bx + 17, by + 6, BG, 3)
            font.text(fb, item, x + 44, ry + (rh - font.height) // 2,
                      MUTED if on else TXT, PANEL)
            self._hit(x, ry, w, rh, ("tick", item))

    # -- shared: steppers and edits waiting for the hub -------------------------

    def _stepper(self, x, y, text, minus, plus, w=58, h=30, enabled=True):
        """[-] value [+], 30 + w + 30 wide."""
        self._button(x, y, 30, h, "", minus, enabled=enabled, icon="minus")
        rrect(self.fb, x + 32, y, w - 4, h, 8, PANEL2)
        font = self._fit(text, w - 10, (self.f.mdb, self.f.md, self.f.sm))
        font.text(self.fb, text, x + 30 + w // 2, y + (h - font.height) // 2, TXT, PANEL2, 1)
        self._button(x + 30 + w, y, 30, h, "", plus, enabled=enabled, icon="plus")

    def _held(self, key, hub_value):
        """What the screen should show for a setting just changed here: the new
        value for a few seconds, until the hub's own report catches up."""
        v = self._pending.get(key)
        if v is not None and self.mono and self.mono() < v[1]:
            return v[0]
        return hub_value

    def _hold(self, key, value):
        self._pending[key] = (value, (self.mono() if self.mono else 0) + 8)

    # -- Power: the last 24 hours ------------------------------------------------

    def _page_power_day(self):
        fb, f = self.fb, self.f
        d = self.data or {}
        x, y, w, h = 8, BODY_Y + 8, W - 16, 142
        self._card(x, y, w, h, "Battery · last 24 hours")
        self._button(x + w - 66, y + 6, 58, 26, "Now", ("pview", "flow"))
        px0, py0, pw, ph = x + 54, y + 40, w - 70, h - 64
        for pct in (0, 50, 100):
            yy = py0 + ph - ph * pct // 100
            fb.hline(px0, yy, pw, LINE)
            f.sm.text(fb, "%d%%" % pct, px0 - 6, yy - 8, MUTED, PANEL, 2)
        for i, lab in enumerate(("24 h ago", "12 h", "now")):
            f.sm.text(fb, lab, px0 + pw * i // 2, py0 + ph + 4, MUTED, PANEL, (0, 1, 2)[i])
        t_now = self.now() if self.now else None
        rows = self.history or []
        if not rows or t_now is None:
            f.md.text(fb, "No history yet", px0 + pw // 2, py0 + ph // 2 - 10, MUTED, PANEL, 1)
        else:
            last = None
            for r in rows:
                t, soc = r[0], r[3]
                if soc is None or t < t_now - 86400:
                    last = None
                    continue
                xx = px0 + int(pw * (t - (t_now - 86400)) / 86400)
                yy = py0 + ph - int(ph * soc / 100)
                # a gap of over half an hour in the record is drawn as a gap
                if last and t - last[2] < 1800:
                    gfx.thick_line(fb, last[0], last[1], xx, yy, GREEN, 3)
                last = (xx, yy, t)
        # energy since midnight
        td = d.get("today") or {}
        y2 = y + h + 8
        self._card(x, y2, w, TAB_Y - 8 - y2, "Energy today")
        cols = (("Solar", td.get("solar"), BRAND), ("Alternator", td.get("alt"), CYAN),
                ("Mains", td.get("mains"), BLUE), ("Used", td.get("load"), TXT))
        cw = (w - 24) // 4
        for i, (lab, wh, col) in enumerate(cols):
            cx = x + 12 + i * cw
            f.sm.text(fb, lab, cx, y2 + 28, MUTED, PANEL)
            if wh is None:
                txt = DASH
            elif wh >= 1000:
                txt = "%.1f kWh" % (wh / 1000)
            else:
                txt = "%d Wh" % wh
            f.mdb.text(fb, txt, cx, y2 + 46, col if wh else MUTED, PANEL)

    # -- Forecast ----------------------------------------------------------------

    def _page_forecast(self):
        fb, f = self.fb, self.f
        self._title = "Forecast"
        wx = self.weather or {}
        data = wx.get("data") or {}
        place = (wx.get("meta") or {}).get("place")
        if place:
            self._title = "Forecast · " + place
        hr = data.get("hourly") or {}
        times = hr.get("time") or []
        if not times:
            self._placeholder("No forecast yet", "The hub fetches it when it is online")
            return
        # the forecast's own times are local (timezone=auto); find this hour
        p = self.parts()
        start = 0
        if p:
            key = "%04d-%02d-%02dT%02d" % (p[0], p[1], p[2], p[3])
            for i, t in enumerate(times):
                if t[:13] >= key:
                    start = i
                    break
        x, y, w, h = 8, BODY_Y + 8, W - 16, 116
        self._card(x, y, w, h, "Next 12 hours")
        # A small chart rather than twelve columns of figures, which do not fit
        # in 36 px each: the temperature as a line, labelled every three hours;
        # the chance of rain as blue bars along the bottom.
        temps = hr.get("temperature_2m") or []
        rain = hr.get("precipitation_probability") or []
        pts = []
        for k in range(12):
            i = start + k
            if i < len(times) and i < len(temps) and temps[i] is not None:
                pts.append((k, i, temps[i]))
        if pts:
            lo = min(p[2] for p in pts)
            hi = max(p[2] for p in pts)
            span = max(hi - lo, 4)                     # a flat day still reads flat
            gx0, gw = x + 28, w - 56
            gy0, gh = y + 58, 16                       # the line's band
            step = gw / 11

            def at(k, t):
                return gx0 + k * step, gy0 + gh - (t - lo) * gh / span

            prev = None
            for k, i, t in pts:
                px, py = at(k, t)
                if prev:
                    self.aa.line(prev[0], prev[1], px, py, 2.5, FLAME)
                prev = (px, py)
            for k, i, t in pts:
                px, py = at(k, t)
                if k % 3 == 0:
                    self.aa.disc(px, py, 3.5, FLAME)
                    self.aa.disc(px, py, 1.5, PANEL)
                    f.sm.text(fb, "%d\u00b0" % round(t), int(px), int(py) - 22, TXT, PANEL, 1)
                    f.sm.text(fb, times[i][11:13], int(px), y + h - 18, MUTED, PANEL, 1)
                # rain chance: a bar up to 14 px high, just above the hour labels
                if i < len(rain) and rain[i]:
                    bh_ = max(2, int(14 * rain[i] / 100))
                    rrect(fb, int(px) - 3, y + h - 22 - bh_, 6, bh_, 2,
                          BLUE if rain[i] >= 30 else gfx.blend(PANEL, BLUE, 0.5))
            f.sm.text(fb, "bars: chance of rain", x + w - 14, y + 10, gfx.blend(PANEL, BLUE, 0.8), PANEL, 2)
        # three days
        dl = data.get("daily") or {}
        dt = dl.get("time") or []
        y2 = y + h + 8
        dw = (w - 16) // 3
        for k in range(min(3, len(dt))):
            dx = x + k * (dw + 8)
            self._card(dx, y2, dw, TAB_Y - 8 - y2)
            yy, mm, dd = int(dt[k][:4]), int(dt[k][5:7]), int(dt[k][8:10])
            name = "Today" if k == 0 else DAYS[(_days_from_civil(yy, mm, dd) + 3) % 7][:3]
            f.mdb.text(fb, name, dx + 12, y2 + 8, TXT, PANEL)
            code = (dl.get("weather_code") or [None] * 3)[k]
            self._wx_icon(dx + 40, y2 + 58, code, False)
            hi = (dl.get("temperature_2m_max") or [None] * 3)[k]
            lo = (dl.get("temperature_2m_min") or [None] * 3)[k]
            f.lg.text(fb, _num(hi, "%d") + "°", dx + dw - 12, y2 + 32, TXT, PANEL, 2)
            f.md.text(fb, _num(lo, "%d") + "°", dx + dw - 12, y2 + 66, MUTED, PANEL, 2)

    # -- Kitchen timer -------------------------------------------------------------

    def kclock(self):
        """The kitchen timer's clock: the hub's time, which a phone setting the
        timer shares; this board's own only until the hub has been heard."""
        t = self.now() if self.now else None
        return t if t is not None else (self.mono() if self.mono else 0)

    def kt_left(self):
        if self.kt_end is not None:
            return max(0, int(self.kt_end - self.kclock()))
        if self.kt_paused is not None:
            return self.kt_paused
        return self.kt_set

    def _kt(self, arg):
        running = self.kt_end is not None
        if arg == "start":
            if running:                                  # pause
                self.kt_paused = self.kt_left()
                self.kt_end = None
            else:
                self.kt_end = self.kclock() + self.kt_left()
                self.kt_paused = None
        elif arg == "reset":
            self.kt_end = self.kt_paused = None
        elif arg == "ack":
            self.kt_done = False
        else:                                            # +/- seconds
            if running:
                self.kt_end = max(self.kclock() + 1, self.kt_end + arg)
            elif self.kt_paused is not None:
                self.kt_paused = max(10, self.kt_paused + arg)
            else:
                self.kt_set = max(10, min(5999, self.kt_set + arg))

    def _page_timer(self):
        fb, f = self.fb, self.f
        self._title = "Kitchen timer"
        self._chip = ("Alarm", "alarm", ("clockview", "alarm"))
        left = self.kt_left()
        running = self.kt_end is not None
        col = BRAND if running else (AMBER if self.kt_paused is not None else TXT)
        f.xxl.text(fb, "%02d:%02d" % (left // 60, left % 60), W // 2, BODY_Y + 30, col, BG, 1)
        state = "Running" if running else ("Paused" if self.kt_paused is not None else "Set the time")
        f.md.text(fb, state, W // 2, BODY_Y + 108, MUTED, BG, 1)
        bw, bh, gap = 106, 44, 8
        x0 = (W - 4 * bw - 3 * gap) // 2
        for i, (lab, secs) in enumerate((("– 1 min", -60), ("+ 1 min", 60),
                                         ("+ 5 min", 300), ("+ 10 min", 600))):
            self._button(x0 + i * (bw + gap), BODY_Y + 140, bw, bh, lab, ("kt", secs))
        self._button(x0, BODY_Y + 192, 2 * bw + gap, bh, "Reset", ("kt", "reset"))
        self._button(x0 + 2 * (bw + gap), BODY_Y + 192, 2 * bw + gap, bh,
                     "Pause" if running else ("Resume" if self.kt_paused is not None else "Start"),
                     ("kt", "start"), fg=TXT,
                     back=gfx.blend(PANEL2, GREEN, 0.55) if not running else PANEL2)

    def _kt_done(self):
        fb, f = self.fb, self.f
        fb.fill_rect(0, 0, W, H, gfx.blend(BG, BRAND, 0.2))
        x, y, w, h = 70, 70, 340, 180
        self._panel(x, y, w, h)
        f.lg.text(fb, "Time's up", x + w // 2, y + 26, BRAND, PANEL, 1)
        f.md.text(fb, "The kitchen timer has finished.", x + w // 2, y + 70, TXT, PANEL, 1)
        self._button(x + 90, y + h - 66, w - 180, 50, "OK", ("kt", "ack"), fg=BG, back=BRAND)

    # -- Heater schedule: two timers and frost protection --------------------------

    def _timers(self):
        return self._held("timers", (self.data or {}).get("timers") or [])

    def _timer_edit(self, tid, key, arg):
        tl = [dict(t) for t in self._timers()]
        for t in tl:
            if t.get("id") != tid:
                continue
            if key == "on":
                t["on"] = not t.get("on")
            elif key == "at":
                hh, mm = [int(v) for v in t.get("at", "07:00").split(":")]
                m = (hh * 60 + mm + arg) % 1440
                t["at"] = "%02d:%02d" % (m // 60, m % 60)
            elif key == "water":
                t["water"] = t.get("water", 2) % 3 + 1
            else:
                lo, hi = {"temp": (5, 35), "run_min": (15, 240)}[key]
                t[key] = max(lo, min(hi, t.get(key, lo) + arg))
        self._hold("timers", tl)
        return ("post", "/api/timers", {"timers": tl})

    def _ah(self):
        return self._held("autoheat", (self.data or {}).get("autoheat") or {})

    def _ah_edit(self, key, arg):
        a = dict(self._ah())
        if key == "armed":
            a["armed"] = not a.get("armed")
        else:
            lo, hi = {"on_below": (-20, 25), "target_c": (5, 35)}[key]
            a[key] = max(lo, min(hi, (a.get(key) or 0) + arg))
            if key == "on_below":
                a["off_above"] = a["on_below"] + 4      # keep the hub's gap sensible
        self._hold("autoheat", a)
        body = {k: a[k] for k in ("armed", "on_below", "off_above", "target_c") if k in a}
        return ("post", "/api/autoheat", body)

    def _toggle(self, x, y, on, what, on_col):
        self._button(x, y, 76, 30, "On" if on else "Off", what,
                     fg=BG if on else MUTED, back=on_col if on else PANEL2)

    def _page_fan(self):
        """The heater's fan: its speed for the next command, and the fan on
        its own - ventilation, no flame - to move air round the van."""
        fb, f = self.fb, self.f
        self._title = "Heater fan"
        self._chip = [("Heater", "flame", ("sub", None))]
        h = self._heater()
        have = h.get("connected") or h.get("remembered")
        running = self._mode(h) if have else None
        en = not self.busy
        x, w = 8, W - 16
        # the speed: four big buttons
        y = BODY_Y + 8
        self._card(x, y, w, 112, "Fan speed")
        cur = self._fan()
        bw = (w - 24 - 3 * 8) // 4
        for i in range(4):
            lit = cur == i + 1
            self._button(x + 12 + i * (bw + 8), y + 30, bw, 52, "%d" % (i + 1), ("fanlvl", i + 1),
                         fg=gfx.BLACK if lit else TXT, back=BRAND if lit else PANEL2,
                         enabled=en, big=True)
        rep_lvl = h.get("fan_level") if running else None
        if running and rep_lvl and rep_lvl != cur:
            note = "Running at %d - press Apply to change it" % rep_lvl
        elif running:
            note = "Running at this speed"
        else:
            note = "Used when the heater or fan is next started"
        f.sm.text(fb, note, x + w // 2, y + 88, MUTED, PANEL, 1)
        # the fan on its own, and its way off
        y2 = y + 120
        self._card(x, y2, w, TAB_Y - y2 - 8, "Fan only")
        f.sm.text(fb, "No flame, no heat", x + w - 14, y2 + 10, MUTED, PANEL, 2)
        by, bh = y2 + 34, 52
        vent = running == "vent"
        if vent:
            self._button(x + 12, by, 210, bh, "Fan running", ("heat", "vent"), icon="fan",
                         fg=gfx.BLACK, back=BLUE, enabled=en)
        else:
            self._button(x + 12, by, 210, bh, "Fan only", ("heat", "vent"), icon="fan",
                         enabled=en)
        if running and rep_lvl and rep_lvl != cur:
            # send the running mode again, with the new speed
            self._button(x + 230, by, 112, bh, "Apply", ("heat", running), enabled=en)
        if running:
            self._button(x + w - 12 - 110, by, 110, bh, "Off", ("heat", "off"), icon="power",
                         back=gfx.blend(PANEL2, RED, 0.5), enabled=en)

    def _page_schedule(self):
        fb, f = self.fb, self.f
        self._title = "Heater schedule"
        x, w, ch = 8, W - 16, 72
        by = {t.get("id"): t for t in self._timers()}
        rows = (("warm", "Morning warm-up"), ("water", "Morning hot water"))
        for i, (tid, name) in enumerate(rows):
            y = BODY_Y + 8 + i * (ch + 4)
            self._card(x, y, w, ch)
            t = by.get(tid)
            f.mdb.text(fb, name, x + 12, y + 8, TXT, PANEL)
            if t is None:
                f.sm.text(fb, "Waiting for the hub", x + w - 12, y + 12, MUTED, PANEL, 2)
                continue
            note = "running" if t.get("running") else (t.get("last") or "")
            while note and f.sm.width(note) > 150:
                note = note[:-2] + "…"
            if note:
                f.sm.text(fb, note, x + w - 98, y + 12, FLAME if t.get("running") else MUTED,
                          PANEL, 2)
            self._toggle(x + w - 88, y + 6, t.get("on"), ("tm", (tid, "on", 0)), FLAME)
            cy = y + 38
            self._stepper(x + 12, cy, t.get("at", DASH), ("tm", (tid, "at", -15)),
                          ("tm", (tid, "at", 15)))
            if tid == "warm":
                self._stepper(x + 142, cy, "%d°C" % t.get("temp", 20),
                              ("tm", (tid, "temp", -1)), ("tm", (tid, "temp", 1)))
            else:
                self._button(x + 142, cy, 124, 30, "Water " + WATER.get(t.get("water", 2), "?"),
                             ("tm", (tid, "water", 0)))
            self._stepper(x + 272, cy, "%d min" % t.get("run_min", 60),
                          ("tm", (tid, "run_min", -15)), ("tm", (tid, "run_min", 15)), w=70)
        # frost protection
        y = BODY_Y + 8 + 2 * (ch + 4)
        self._card(x, y, w, TAB_Y - 8 - y)
        a = self._ah()
        f.mdb.text(fb, "Frost protection", x + 12, y + 8, TXT, PANEL)
        if a:
            if a.get("active"):
                note, col = "heating now", FLAME
            else:
                t = a.get("temp_c")
                note, col = ("probe %s°C" % _num(t, "%d")), MUTED
            f.sm.text(fb, note, x + w - 98, y + 12, col, PANEL, 2)
            self._toggle(x + w - 88, y + 6, a.get("armed"), ("ah", ("armed", 0)), BLUE)
            cy = y + 38
            f.sm.text(fb, "below", x + 12, cy + 7, MUTED, PANEL)
            self._stepper(x + 58, cy, "%d°C" % a.get("on_below", 5),
                          ("ah", ("on_below", -1)), ("ah", ("on_below", 1)))
            f.sm.text(fb, "heat to", x + 196, cy + 7, MUTED, PANEL)
            self._stepper(x + 256, cy, "%d°C" % a.get("target_c", 18),
                          ("ah", ("target_c", -1)), ("ah", ("target_c", 1)))
        else:
            f.sm.text(fb, "Waiting for the hub", x + w - 12, y + 12, MUTED, PANEL, 2)

    # -- Lights: the ambient light, and panic -------------------------------------

    WHEEL_R = 74
    WARM = (255, 150, 60)                # the warm-white button's colour

    def ambient_rgb(self, t=None):
        """The ambient light's colour at its brightness, or None when off.
        t (seconds) turns the hue for the colour cycle: once round a minute."""
        am = self.ambient
        if not am.get("on"):
            return None
        k = am.get("bright", 50) / 100
        if am.get("warm") and not am.get("cycle"):
            r, g, b = self.WARM
        else:
            h = am.get("h", 30)
            if am.get("cycle") and t is not None:
                h = (h + t * 6) % 360
            r, g, b = hsv(h, am.get("s", 80) / 100)
        return int(r * k), int(g * k), int(b * k)

    def wheel_steps(self):
        """Build the colour wheel image a few rows at a time (a generator:
        main.py runs it in the background). Hue round the edge, paler towards
        the middle; the edge is smoothed into the card colour."""
        import math as m
        R = self.WHEEL_R
        size = 2 * R + 4
        buf = bytearray(size * size * 2)
        img = framebuf.FrameBuffer(buf, size, size, framebuf.RGB565)
        img.fill(PANEL)
        c = size / 2
        pr, pg, pb = gfx._unswap(PANEL)
        for y in range(size):
            dy = y + 0.5 - c
            for x in range(size):
                dx = x + 0.5 - c
                d = m.sqrt(dx * dx + dy * dy)
                if d > R + 0.5:
                    continue
                hue = (m.degrees(m.atan2(dx, -dy)) + 360) % 360
                r, g, b = hsv(hue, min(1.0, d / R))
                cov = min(1.0, R + 0.5 - d)
                if cov < 1:
                    r = int(pr + (r - pr) * cov)
                    g = int(pg + (g - pg) * cov)
                    b = int(pb + (b - pb) * cov)
                img.pixel(x, y, gfx.rgb(r, g, b))
            yield None                       # a row at a time: ~35 ms on the display
        self.wheel_img = img
        self.dirty = True
        yield img

    def _page_lights(self):
        fb, f, a = self.fb, self.f, self.aa
        am = self.ambient
        x, y, w, h = 8, BODY_Y + 8, 300, TAB_Y - BODY_Y - 16
        self._card(x, y, w, h, "Ambient light")
        on = am.get("on")
        # the wheel: tap it to choose; a marker shows the choice
        R = self.WHEEL_R
        cx, cy = x + 14 + R, y + 34 + R
        if self.wheel_img:
            fb.blit(self.wheel_img, cx - R - 2, cy - R - 2)
            if not on:                               # dimmed while the light is off
                pass
            if not am.get("warm"):
                ang = math.radians(am.get("h", 30))
                rr = R * am.get("s", 80) / 100
                mx, my = cx + math.sin(ang) * rr, cy - math.cos(ang) * rr
                a.disc(mx, my, 8, gfx.BLACK)
                a.disc(mx, my, 6.5, TXT)
                a.disc(mx, my, 4.5, gfx.rgb(*hsv(am.get("h", 30), am.get("s", 80) / 100)))
            self._hit(cx - R, cy - R, 2 * R, 2 * R, ("wheel", 0))
        else:
            a.ring(cx, cy, R - 2, R, LINE)
            f.sm.text(fb, "Preparing\u2026", cx, cy - 8, MUTED, PANEL, 1)
        # the controls beside it
        bx, bw = x + 2 * R + 30, w - 2 * R - 42
        self._button(bx, y + 8, bw, 32, "On" if on else "Off", ("amb", "on"),
                     fg=gfx.BLACK if on else MUTED, back=BRAND if on else PANEL2)
        cyc = am.get("cycle")
        self._button(bx, y + 48, bw, 32, "Cycle", ("amb", "cycle"),
                     fg=gfx.BLACK if cyc else TXT, back=BRAND if cyc else PANEL2)
        warm = am.get("warm") and not cyc
        self._button(bx, y + 88, bw, 32, "Warm", ("amb", "warm"),
                     fg=gfx.BLACK if warm else TXT,
                     back=gfx.rgb(*self.WARM) if warm else PANEL2)
        f.sm.text(fb, "Brightness", bx + bw // 2, y + 132, MUTED, PANEL, 1)
        f.sm.text(fb, "%d%%" % am.get("bright", 50), bx + bw // 2, y + 176, TXT, PANEL, 1)
        self._slider("amb", bx + 10, y + 158, bw - 20, 10,
                     (am.get("bright", 50) - 10) / 90, BRAND)

        # panic
        x2, w2 = x + w + 8, W - 8 - (x + w + 8)
        self._card(x2, y, w2, h, "Security")
        cx, cy, r = x2 + w2 // 2, y + 92, 48
        a.disc(cx, cy + 4, r, gfx.BLACK, alpha=120)
        a.disc(cx, cy, r, gfx.blend(PANEL, RED, 0.85))
        # while it is being held, a ring fills round the button
        if self.panic_arming is not None and self.mono:
            frac = min(1.0, (self.mono_ms() - self.panic_arming) / self.PANIC_HOLD_MS)
            if frac > 0:
                a.arc(cx, cy, r + 4, r + 9, 0, frac * 6.283, TXT, caps=False)
        self._icon("siren", cx - 14, cy - 26, TXT, 28, bg=gfx.blend(PANEL, RED, 0.85))
        f.mdb.text(fb, "Hold", cx, cy + 8, TXT, gfx.blend(PANEL, RED, 0.85), 1)
        f.sm.text(fb, "Hold for the siren", cx, y + h - 70, MUTED, PANEL, 1)
        self._hit(cx - r, cy - r, 2 * r, 2 * r, ("panic_hold", 0))
        # guard: armed when leaving the van; the hub watches the tilt and engine
        g = self.guard()
        st = g.get("state", "off") if g.get("on") else "off"
        label, fg, back = {
            "off": ("Guard off", MUTED, PANEL2),
            "arming": ("Arming %ds" % (g.get("arming_s") or 0), gfx.BLACK, AMBER),
            "armed": ("Guarding", gfx.BLACK, GREEN),
            "tripped": ("Guard alarm", TXT, RED),
        }.get(st, ("Guard off", MUTED, PANEL2))
        self._button(x2 + 10, y + h - 48, w2 - 20, 38, label, ("guard", 0), fg=fg, back=back)

    def _amb(self, arg):
        am = self.ambient
        if arg == "on":
            am["on"] = not am.get("on")
        elif arg == "cycle":
            am["cycle"] = not am.get("cycle")
            am["on"] = True
        elif arg == "warm":
            am["warm"] = True
            am["cycle"] = False
            am["on"] = True
        elif arg[0] == "bright":
            am["bright"] = max(10, min(100, am.get("bright", 50) + arg[1]))
        return ("prefs", "ambient")

    PANIC_HOLD_MS = 900

    def _panic_screen(self):
        fb, f = self.fb, self.f
        # the screen flashes with the beacon: redrawn twice a second by main.py
        flash = (self.mono_ms() // 500) % 2 if self.mono else 0
        fb.fill(gfx.blend(BG, RED if flash else BLUE, 0.45))
        x, y, w, h = 60, 50, 360, 220
        self._panel(x, y, w, h)
        f.lg.text(fb, "PANIC", x + w // 2, y + 22, RED, PANEL, 1)
        f.md.text(fb, "Beacon and siren are on", x + w // 2, y + 66, TXT, PANEL, 1)
        self._button(x + 40, y + h - 104, w - 80, 80, "STOP", ("panic_stop", 0),
                     fg=TXT, back=RED, big=True)

    # -- Clock: the alarm clock ------------------------------------------------------

    def share(self, part):
        """One part of the settings the hub keeps for every screen."""
        if part == "ambient":
            return dict(self.ambient)
        if part == "alarm_clock":
            return dict(self.alarm_clock)
        if part == "kitchen":
            return {"end": int(self.kt_end or 0), "paused": int(self.kt_paused or 0),
                    "set": self.kt_set}
        if part == "ticks":
            return sorted(self.ticks)
        if part == "brightness":
            return self.brightness
        if part == "night":
            return self.night_mode
        if part == "dim":
            return self.dim_on
        if part == "offmode":
            return self.offmode
        if part == "switches":
            return list(self.switches)
        return None

    def guard(self):
        return self._held("guard", (self.data or {}).get("guard") or {})

    AC_DAYS = ("every", "weekdays", "weekends")
    AC_DAY_LABEL = {"every": "Every day", "weekdays": "Weekdays", "weekends": "Weekends"}

    def _ac(self, arg):
        ac = self.alarm_clock
        if arg == "on":
            ac["on"] = not ac.get("on")
        elif arg == "days":
            i = self.AC_DAYS.index(ac.get("days", "every")) if ac.get("days") in self.AC_DAYS else 0
            ac["days"] = self.AC_DAYS[(i + 1) % 3]
        elif arg == "warm":
            ac["warm"] = {0: 15, 15: 30, 30: 45, 45: 60}.get(ac.get("warm") or 0, 0)
        elif arg == "snooze":
            self.ac_ringing = False
            self.ac_snooze = (self.now() or 0) + 9 * 60
            return None
        elif arg == "stop":
            self.ac_ringing = False
            self.ac_snooze = None
            return None
        else:                                    # minutes to move the time by
            hh, mm = [int(v) for v in ac.get("at", "07:00").split(":")]
            m = (hh * 60 + mm + arg) % 1440
            ac["at"] = "%02d:%02d" % (m // 60, m % 60)
            ac["on"] = True                      # setting a time means you want it
        return ("prefs", "alarm_clock")

    def ac_next(self):
        """Unix time the alarm clock next rings, or None."""
        ac = self.alarm_clock
        t = self.now() if self.now else None
        if not ac.get("on") or t is None:
            return None
        hh, mm = [int(v) for v in ac.get("at", "07:00").split(":")]
        p = self.parts(t)
        base = t - (p[3] * 3600 + p[4] * 60 + p[5])         # local midnight
        for d in range(8):
            when = base + d * 86400 + hh * 3600 + mm * 60
            if when <= t:
                continue
            wd = (p[6] + d) % 7
            days = ac.get("days", "every")
            if days == "weekdays" and wd >= 5 or days == "weekends" and wd < 5:
                continue
            return when
        return None

    def _page_alarm(self):
        fb, f = self.fb, self.f
        self._title = "Alarm clock"
        self._chip = ("Timer", "clock", ("clockview", "timer"))
        ac = self.alarm_clock
        on = ac.get("on")
        f.xxl.text(fb, ac.get("at", "07:00"), W // 2, BODY_Y + 18,
                   BRAND if on else MUTED, self.bgc(BODY_Y + 50), 1)
        nxt = self.ac_next()
        if self.ac_snooze:
            note = "Snoozing"
        elif nxt and self.now:
            note = "Rings in " + _hm((nxt - self.now()) / 3600)
            wk = ((self.data or {}).get("wake") or {}).get("at")
            if ac.get("warm") and wk:
                note += " \u00b7 heater at " + wk
        else:
            note = "Off" if not on else "Waiting for the time from the hub"
        f.md.text(fb, note, W // 2, BODY_Y + 100, MUTED, self.bgc(BODY_Y + 110), 1)
        bw, bh, gap = 106, 44, 8
        x0 = (W - 4 * bw - 3 * gap) // 2
        for i, (lab, mins) in enumerate((("– 1 h", -60), ("+ 1 h", 60),
                                         ("– 5 min", -5), ("+ 5 min", 5))):
            self._button(x0 + i * (bw + gap), BODY_Y + 132, bw, bh, lab, ("ac", mins))
        # the second row in thirds: days, the heater warm-up, on/off
        tw = (4 * bw + 3 * gap - 2 * gap) // 3
        self._button(x0, BODY_Y + 184, tw, bh,
                     self.AC_DAY_LABEL.get(ac.get("days", "every"), "Every day"), ("ac", "days"))
        wm = ac.get("warm") or 0
        self._button(x0 + tw + gap, BODY_Y + 184, tw, bh,
                     ("Warm %d min" % wm) if wm else "No warm-up", ("ac", "warm"),
                     icon="flame", fg=TXT if wm else MUTED,
                     back=gfx.blend(PANEL2, FLAME, 0.35) if wm else PANEL2)
        self._button(x0 + 2 * (tw + gap), BODY_Y + 184, tw, bh,
                     "Alarm on" if on else "Alarm off", ("ac", "on"), icon="alarm",
                     fg=gfx.BLACK if on else MUTED, back=BRAND if on else PANEL2)

    def _ac_ringing(self):
        fb, f = self.fb, self.f
        fb.fill_rect(0, 0, W, H, gfx.blend(BG_BOT, BRAND, 0.18))
        x, y, w, h = 60, 50, 360, 220
        self._panel(x, y, w, h)
        hm = self.clock()
        f.xl.text(fb, "%02d:%02d" % hm if hm else "--:--", x + w // 2, y + 20, BRAND, PANEL, 1)
        f.md.text(fb, "Good morning", x + w // 2, y + 76, TXT, PANEL, 1)
        bw = (w - 36) // 2
        self._button(x + 12, y + h - 74, bw, 60, "Snooze 9 min", ("ac", "snooze"))
        self._button(x + 24 + bw, y + h - 74, bw, 60, "Stop", ("ac", "stop"),
                     fg=gfx.BLACK, back=BRAND)

    def _st_edit(self, arg):
        st = dict(self._held("storage", (self.data or {}).get("storage") or {}))
        if arg == "on":
            st["on"] = not st.get("on")
            body = {"on": st["on"]}
        else:
            st["target"] = max(30, min(95, st.get("target", 80) + arg))
            body = {"target": st["target"]}
        self._hold("storage", st)
        return ("post", "/api/storage", body)

    # -- Battery ---------------------------------------------------------------

    def _page_battery(self):
        fb, f = self.fb, self.f
        d = self.data or {}
        if not d.get("connected"):
            self._placeholder("Battery not connected", "Waiting for the BMS")
            return
        cells = d.get("cells_mv") or []
        x, y, w, h = 8, BODY_Y + 8, 262, TAB_Y - BODY_Y - 16
        self._card(x, y, w, h, "Cells")
        if cells:
            lo, hi = min(cells), max(cells)
            spread = hi - lo
            scol = GREEN if spread < 20 else AMBER if spread < 50 else RED
            f.sm.text(fb, "spread %d mV" % spread, x + w - 12, y + 10, scol, PANEL, 2)
            n = len(cells)
            row = min(40, (h - 44) // n)
            for i, mv in enumerate(cells):
                ry = y + 38 + i * row
                f.md.text(fb, "%d" % (i + 1), x + 12, ry + 4, MUTED, PANEL)
                f.mdb.text(fb, "%.3f V" % (mv / 1000), x + 36, ry + 4,
                           RED if mv in (lo, hi) and spread >= 50 else TXT, PANEL)
                # LiFePO4 working range, 3.0 to 3.65 V
                bar(fb, x + 128, ry + 8, w - 140, 12, (mv - 3000) / 650,
                    BLUE if mv == hi and spread >= 20 else GREEN)
        else:
            f.md.text(fb, "No cell data", x + 12, y + 44, MUTED, PANEL)

        x, w = 278, 194
        self._card(x, y, w, h, "Pack")
        lines = [
            ("Charge", "%s / %s Ah" % (_num(d.get("residual_ah")), _num(d.get("nominal_ah"), "%d"))),
        ]
        temps = d.get("temps_c") or []
        if temps:
            lines.append(("Temp \u00b7 %s cycles" % _num(d.get("cycles"), "%d"),
                          " / ".join("%d°" % t for t in temps[:3])))
        ty = y + 34
        for label, val in lines:
            f.sm.text(fb, label, x + 12, ty, MUTED, PANEL)
            f.mdb.text(fb, val, x + 12, ty + 18, TXT, PANEL)
            ty += 46
        # storage mode: the hub holds the battery near a target by switching
        # charging off and on - kinder to lithium cells left for weeks
        st = self._held("storage", d.get("storage") or {})
        f.sm.text(fb, "Storage mode" + (" \u00b7 holding" if st.get("holding") else ""),
                  x + 12, ty, MUTED, PANEL)
        on = st.get("on")
        self._button(x + 12, ty + 18, 52, 30, "On" if on else "Off", ("st", "on"),
                     fg=BG if on else MUTED, back=BLUE if on else PANEL2)
        self._stepper(x + 70, ty + 18, "%d%%" % st.get("target", 80), ("st", -5), ("st", 5),
                      w=50)
        ty += 56
        # the BMS's two switches: a red one means it has cut that direction off
        dx = x + 12
        for label, on in (("Charge", d.get("charge_fet")),
                          ("Discharge", d.get("discharge_fet"))):
            dot(fb, dx + 5, ty + 8, 5, GREEN if on else RED)
            dx += 14 + f.sm.text(fb, label, dx + 14, ty, TXT if on else RED, PANEL) + 12
