"""Render the display's pages to PNG on the PC, from sample data.

The display code draws with MicroPython's framebuf, which does not exist on a
PC. This supplies a stand-in with the same behaviour for the handful of calls
gfx.py and ui.py use, so the real ui.py renders here unchanged - the PNGs show
exactly the layout the device will draw, without flashing anything.

Run:  python tools/preview_display.py [outdir]
"""

import os
import sys
import types

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DISPLAY = os.path.join(ROOT, "display")


# ---- a minimal framebuf -------------------------------------------------------

class FrameBuffer:
    RGB565 = 1
    GS4_HMSB = 2

    def __init__(self, buf, w, h, fmt):
        self.buf, self.w, self.h, self.fmt = buf, w, h, fmt

    def pixel(self, x, y, c=None):
        if not (0 <= x < self.w and 0 <= y < self.h):
            return None
        if self.fmt == FrameBuffer.RGB565:
            i = (y * self.w + x) * 2
            if c is None:
                return self.buf[i] | (self.buf[i + 1] << 8)
            self.buf[i] = c & 255
            self.buf[i + 1] = (c >> 8) & 255
        else:                                          # GS4_HMSB
            i = (y * self.w + x) >> 1
            b = self.buf[i]
            if c is None:
                return (b >> 4) if x % 2 == 0 else (b & 15)
            if x % 2 == 0:
                self.buf[i] = (b & 0x0F) | ((c & 15) << 4)
            else:
                self.buf[i] = (b & 0xF0) | (c & 15)

    def fill_rect(self, x, y, w, h, c):
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(self.w, x + w), min(self.h, y + h)
        if x1 <= x0 or y1 <= y0:
            return
        lo, hi = c & 255, (c >> 8) & 255
        row = bytes((lo, hi)) * (x1 - x0)
        for yy in range(y0, y1):
            i = (yy * self.w + x0) * 2
            self.buf[i:i + len(row)] = row

    def fill(self, c):
        self.fill_rect(0, 0, self.w, self.h, c)

    def hline(self, x, y, w, c):
        self.fill_rect(x, y, w, 1, c)

    def vline(self, x, y, h, c):
        self.fill_rect(x, y, 1, h, c)

    def rect(self, x, y, w, h, c, f=False):
        if f:
            self.fill_rect(x, y, w, h, c)
        else:
            self.hline(x, y, w, c); self.hline(x, y + h - 1, w, c)
            self.vline(x, y, h, c); self.vline(x + w - 1, y, h, c)

    def line(self, x0, y0, x1, y1, c):
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        e = dx + dy
        while True:
            self.pixel(x0, y0, c)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * e
            if e2 >= dy:
                e += dy; x0 += sx
            if e2 <= dx:
                e += dx; y0 += sy

    def ellipse(self, cx, cy, xr, yr, c, f=False, m=15):
        # quadrants as MicroPython numbers them: 1 top-right, 2 top-left,
        # 4 bottom-left, 8 bottom-right
        for dy in range(-yr, yr + 1):
            t = 1 - (dy * dy) / float(yr * yr) if yr else 1
            if t < 0:
                continue
            half = int(round(xr * (t ** 0.5)))
            top = dy <= 0
            bot = dy >= 0
            if f:
                if (top and m & 2) or (bot and m & 4):
                    self.fill_rect(cx - half, cy + dy, half + 1, 1, c)
                if (top and m & 1) or (bot and m & 8):
                    self.fill_rect(cx, cy + dy, half + 1, 1, c)
            else:
                if (top and m & 2) or (bot and m & 4):
                    self.pixel(cx - half, cy + dy, c)
                if (top and m & 1) or (bot and m & 8):
                    self.pixel(cx + half, cy + dy, c)
        if not f:                                      # close the steep parts
            for dx in range(-xr, xr + 1):
                t = 1 - (dx * dx) / float(xr * xr) if xr else 1
                if t < 0:
                    continue
                half = int(round(yr * (t ** 0.5)))
                left, right = dx <= 0, dx >= 0
                if (left and m & 2) or (right and m & 1):
                    self.pixel(cx + dx, cy - half, c)
                if (left and m & 4) or (right and m & 8):
                    self.pixel(cx + dx, cy + half, c)

    def poly(self, x, y, coords, c, f=False):
        pts = [(x + coords[i], y + coords[i + 1]) for i in range(0, len(coords), 2)]
        edges = list(zip(pts, pts[1:] + pts[:1]))
        for (x0, y0), (x1, y1) in edges:
            self.line(x0, y0, x1, y1, c)
        if not f:
            return
        ys = [p[1] for p in pts]
        for yy in range(min(ys), max(ys) + 1):
            xs = sorted(x0 + (yy - y0) * (x1 - x0) / (y1 - y0)
                        for (x0, y0), (x1, y1) in edges
                        if (y0 <= yy < y1) or (y1 <= yy < y0))
            for a, b in zip(xs[::2], xs[1::2]):
                self.fill_rect(int(round(a)), yy, int(round(b)) - int(round(a)) + 1, 1, c)

    def blit(self, src, x, y, key=-1, palette=None):
        for sy in range(src.h):
            for sx in range(src.w):
                c = src.pixel(sx, sy)
                if palette is not None:
                    c = palette.pixel(c, 0)
                if c != key:                           # key is checked after the palette
                    self.pixel(x + sx, y + sy, c)


def install_framebuf():
    m = types.ModuleType("framebuf")
    m.FrameBuffer = FrameBuffer
    m.RGB565 = FrameBuffer.RGB565
    m.GS4_HMSB = FrameBuffer.GS4_HMSB
    sys.modules["framebuf"] = m


def to_png(buf, w, h, path):
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            i = (y * w + x) * 2
            c = buf[i] | (buf[i + 1] << 8)
            c = ((c & 255) << 8) | (c >> 8)           # undo the panel byte swap
            px[x, y] = ((c >> 8) & 0xF8, (c >> 3) & 0xFC, (c << 3) & 0xF8)
    img.save(path)


# ---- sample data, shaped like the hub's /api/data -----------------------------

SAMPLE = {
    "connected": True, "soc": 78, "voltage": 13.31, "current": 6.4, "power_w": 85,
    "residual_ah": 78.2, "nominal_ah": 100, "cycles": 41,
    "cells_mv": [3326, 3331, 3329, 3322], "temps_c": [19, 20],
    "charge_fet": True, "discharge_fet": True,
    "renogy": {"connected": True, "solar_w": 212, "solar_v": 19.4, "alt_w": 0,
               "alt_v": 12.6, "charge_w": 190, "charge_a": 14.2, "state": "MPPT"},
    "victron": {"connected": False},
    "derived": {"load_a": 7.8, "load_w": 104},
    "heater": {"connected": False, "remembered": True, "at": 1790330000, "on": True,
               "mode": "Air", "mode_code": 4, "set_air_c": 20, "air_temp_c": 17, "water_temp_c": 44,
               "hx_temp_c": 96, "supply_v": 13.2, "fault": False, "energy": "Diesel",
               "fan_level": 2, "water_code": 2},
    "level": {"enabled": True, "present": True, "ok": True, "roll": 2.3, "pitch": -0.8,
              "off_by": 2.43, "calibrated": True, "cal_stale": False, "steady": True,
              "temp_c": 21.4},
    "level_scale": {"ok": 1.0, "warn": 20.0, "max": 30.0},
    "alerts": [],
    "today": {"day": "2026-09-25", "solar": 1420, "alt": 380, "mains": 0, "load": 910},
    "storage": {"on": False, "holding": False, "target": 80},
    "autoheat": {"armed": True, "active": False, "on_below": 5, "off_above": 9,
                 "target_c": 18, "temp_c": 12},
    "timers": [
        {"id": "warm", "on": True, "at": "07:00", "action": "air", "temp": 20, "water": 2,
         "run_min": 60, "running": False, "last": "Started at 07:00"},
        {"id": "water", "on": False, "at": "06:45", "action": "water", "temp": 20, "water": 2,
         "run_min": 45, "running": False, "last": ""},
    ],
}


# The biggest numbers the van can show: 250 A and over 3 kW either way, with
# every source flat out - where three- and four-figure values have to fit.
def _extreme(sign):
    amps, volts = 250.0 * sign, 13.0 if sign > 0 else 12.4
    return dict(SAMPLE, current=amps, power_w=round(amps * volts), soc=100 if sign > 0 else 8,
                residual_ah=100 if sign > 0 else 8,
                renogy={"connected": True, "solar_w": 999, "solar_v": 99.9, "solar_a": 49.9, "alt_w": 999,
                        "alt_v": 14.4, "alt_a": 69.9, "charge_w": 1998, "charge_a": 139.9, "state": "MPPT"},
                victron={"connected": True, "power_w": 3000, "current": 249.9},
                derived={"load_a": 249.9, "load_w": 3250},
                stats={"time_to_full_h": 12.25, "time_to_empty_h": 0.4},
                today={"day": "2026-09-25", "solar": 9999, "alt": 9999, "mains": 9999, "load": 99999})


WEATHER = {
    "meta": {"place": "Hawes", "age_s": 900, "stale": False},
    "data": {"current": {"temperature_2m": 14.2, "weather_code": 61, "wind_speed_10m": 7.9},
             "hourly": {"time": ["2026-09-25T%02d:00" % h for h in range(24)] +
                                ["2026-09-26T%02d:00" % h for h in range(24)],
                        "temperature_2m": [9 + (h % 24 > 6 and h % 24 < 18) * 5 for h in range(48)],
                        "precipitation_probability": [(h * 17) % 90 for h in range(48)],
                        "weather_code": [3] * 48},
             "daily": {"time": ["2026-09-25", "2026-09-26", "2026-09-27"],
                       "weather_code": [61, 2, 95],
                       "temperature_2m_max": [15.7, 17.2, 13.1],
                       "temperature_2m_min": [11.7, 9.4, 8.0]}},
}
# a day of history, one row every ten minutes: charge falling overnight, then solar
import math as _m
HISTORY = [[1790330117 - 86400 + i * 600, 13.2, 1.0,
            int(55 + 25 * _m.sin(i / 144 * 2 * _m.pi - 1.2)), 10] for i in range(145)]


def main():
    # not under build/: tools/build_mpy.py empties that folder on every hub build
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "preview")
    os.makedirs(out, exist_ok=True)
    install_framebuf()
    sys.path.insert(0, DISPLAY)
    import gfx
    import ui

    buf = bytearray(480 * 320 * 2)
    fb = FrameBuffer(buf, 480, 320, FrameBuffer.RGB565)
    fonts = gfx.Fonts(os.path.join(DISPLAY, "fonts"))

    # Overflow check: record every card and every piece of text drawn, and
    # report text that starts inside a card but runs out of it. This is the
    # fault a device screenshot showed; checking every render catches it here.
    cards, texts, problems = [], [], []
    orig_card, orig_text = ui.UI._card, gfx.Font.text

    def rec_card(self, x, y, w, h, label=None):
        cards.append((x, y, w, h))
        return orig_card(self, x, y, w, h, label)

    def rec_text(self, fbuf, s, x, y, fg, bg, align=0):
        w = orig_text(self, fbuf, s, x, y, fg, bg, align)
        x0 = x - (w // 2 if align == 1 else w if align == 2 else 0)
        texts.append((s, x0, y, w, self.height, len(cards)))
        return w

    ui.UI._card = rec_card
    orig_panel = ui.UI._panel

    def rec_panel(self, x, y, w, h):
        cards.append((x, y, w, h))
        return orig_panel(self, x, y, w, h)

    ui.UI._panel = rec_panel
    # the power flow's battery holds its figures like a card: check them too
    orig_case = ui.UI._flow_battery_case

    def rec_case(self, bok):
        x, y, w, h, cy = self._flow_battery_geo()
        cards.append((x, y, w, h))
        return orig_case(self, bok)

    ui.UI._flow_battery_case = rec_case
    orig_button = ui.UI._button
    fit_notes = []

    def rec_button(self, x, y, w, h, text, what, fg=None, back=None, enabled=True, icon=None,
                   big=False):
        # Button text must fit inside the button, beside its icon, with 10 px
        # each side; and a label that has to drop to the smallest font is
        # noted, because it will look out of step with its neighbours.
        if text:
            room = w - 20 - (24 if icon else 0)
            fnt = ui.UI._fit(text, room, (self.f.mdb, self.f.md, self.f.sm))
            if fnt.width(text) > room:
                problems.append("button %r is %d px too wide for its %d px button"
                                % (text, fnt.width(text) - room, w))
            elif fnt is self.f.sm:
                fit_notes.append(text)
        kw = {"enabled": enabled, "icon": icon, "big": big}
        if fg is not None:
            kw["fg"] = fg
        if back is not None:
            kw["back"] = back
        return orig_button(self, x, y, w, h, text, what, **kw)

    ui.UI._button = rec_button
    gfx.Font.text = rec_text

    def check(name):
        for s_, x0, y0, w, h, n in texts:
            # only the cards already drawn when the text was: a pop-up drawn
            # later covers the page, it does not contain the page's text
            # the card it belongs to is the one its middle is in - centred text
            # too wide for its card starts outside it, so its start would miss
            # it (the battery's "Full in 12 h 15 m" did exactly that)
            mx, my = x0 + w // 2, y0 + h // 2
            for cx, cy, cw, ch in reversed(cards[:n]):
                if cx <= mx < cx + cw and cy <= my < cy + ch:
                    over = max(x0 + w - (cx + cw - 2), (cx + 2) - x0, y0 + h - (cy + ch))
                    if over > 0:
                        problems.append("%s: %r runs out of its card by %d px" % (name, s_, over))
                    break
            else:
                if x0 < 0 or x0 + w > 480:
                    problems.append("%s: %r runs off the screen" % (name, s_))

    u = ui.UI(fb, fonts, now=lambda: 1790330117,
              logo=os.path.join(DISPLAY, "logo.bin"), mono=lambda: 1000, buf=buf)
    u.history = HISTORY
    u.link = "ok"
    shots = []

    def shot(name):
        cards.clear()
        texts.clear()
        u.render()
        check(name)
        p = os.path.join(out, name + ".png")
        to_png(buf, 480, 320, p)
        shots.append(p)

    u.data = SAMPLE
    u.weather = WEATHER
    for i, name in enumerate(u.pages):
        u.page = i
        shot(name)
    # the switches with icons chosen in the hub's settings: the new ones, and
    # the rest of the list after them
    u.page = u.pages.index("switches")
    for n, icons in enumerate((["fan", "tv", "music", "usb", "cup", "wifi"],
                               ["sun", "flame", "plug", "battery", "engine", "siren"],
                               ["switch", "clock", "home", "drop", "snow", "bulb"])):
        u.data["switch_icons"] = icons
        shot("switches_icons%d" % (n + 1))
    u.data.pop("switch_icons", None)
    # the heater's confirm step, and a page with the hub out of reach
    u.page = u.pages.index("heater")
    u.render()                                         # taps hit what was last drawn
    u.tap(392, 64)                                     # "Air heat"
    shot("heater_confirm")
    u.confirm = None
    u.pair = {"code": "482913"}
    shot("pairing")
    u.pair = {"code": "482913", "done": True}
    shot("paired")
    u.pair = None
    u.busy = "Sending to the heater…"
    shot("heater_busy")
    u.busy = None
    u.page = 0
    u.render()
    u.dbatt = {"v": 3.98, "pct": 80, "state": "charging"}   # the display's own battery
    u.tap(470, 10)                                     # the clock in the header
    shot("brightness")
    u.night_src = (1790456520, 1790404200, "internet")   # sunset, sunrise, source
    shot("brightness_night_times")
    u.night_src = (None, None, None)
    shot("brightness_no_location")
    u.night_src = None
    u.offmode = True; shot("brightness_offmode"); u.offmode = False
    u.render(); u.tap(294, 260)                        # "Shut down"
    assert u.confirm and u.confirm[0] == "shutdown", u.confirm
    shot("shutdown_confirm")
    u.confirm = None; u.bright_open = True
    u.render(); u.tap(186, 260)                        # "Restart"
    assert u.confirm and u.confirm[0] == "restart", u.confirm
    shot("restart_confirm")
    u.confirm = None; u.bright_open = True
    u.bright_open = False
    u.page = 0; u.swipe(-1); shot("logo_page")        # back from Home: the logo
    print("swipe on from the logo ->", (u.swipe(1), u.pages[u.page], u.logo_page)[1:])
    u.page = len(u.pages) - 1; u.logo_page = False
    u.swipe(1); print("on from the last page ->", u.logo_page)
    u.swipe(-1); print("back from the logo ->", u.pages[u.page])
    u.logo_page = False; u.page = 0; u.bright_open = True
    u.bright_open = False
    u.weather = dict(WEATHER, data=dict(WEATHER["data"], current={
        "temperature_2m": -2.4, "weather_code": 73, "wind_speed_10m": 21}))
    shot("home_snow")
    u.weather = WEATHER
    # worst cases: everything out of range (as on the bench), and the steepest
    # tilts the Level page can draw, both ways
    offline = {"connected": False, "renogy": {"connected": False},
               "victron": {"connected": False}, "derived": {}, "heater": {},
               "level": SAMPLE["level"], "level_scale": SAMPLE["level_scale"], "alerts": []}
    u.data = offline
    for name in ("home", "power", "heater"):
        u.page = u.pages.index(name)
        shot(name + "_offline")
    u.page = u.pages.index("power"); u.sub = "battery"; shot("battery_offline"); u.sub = None
    for tag, r, p in (("steep_a", 14.0, 13.0), ("steep_b", -14.0, -13.0)):
        u.data = dict(SAMPLE, level=dict(SAMPLE["level"], roll=r, pitch=p, off_by=19.1))
        u.page = u.pages.index("level")
        shot("level_" + tag)
    u.data = SAMPLE
    u.page = u.pages.index("home")
    u.alerts = [{"id": "mains_while_running", "title": "Still plugged in", "acked": False,
                 "detail": "The engine is running and the mains hook-up is still "
                           "connected. Unplug the lead before moving the van."}]
    u.saver = True
    shot("screensaver_alarm")                          # an alarm shows over it
    u.saver = False
    shot("alarm")
    u.render()
    print("tap Clear alarm ->", u.tap(240, 250))
    shot("alarm_cleared")
    u.alerts = None
    u.data = SAMPLE
    u.page = u.pages.index("power"); u.power_view = "day"; shot("power_day")
    u.power_view = "flow"
    u.data = dict(SAMPLE, power_w=-45, current=-3.4,
                  stats=dict(SAMPLE.get("stats") or {}, time_to_empty_h=11.5))
    shot("power_flow_discharging")
    u.data = dict(u.data, power_w=-2350, current=-180,
                  derived={"load_w": 2410, "load_a": 185}); shot("power_flow_big_load")
    u.data = dict(u.data, power_w=1875, current=140); shot("power_flow_big_charge")
    u.data = dict(u.data, soc=30)
    shot("power_flow_low")
    u.data = dict(u.data, soc=12)
    shot("power_flow_empty")
    u.data = SAMPLE
    u.power_view = "now"; shot("power_details")
    # The states hardly anyone sees, which is where text is most likely to
    # have gone unchecked: no forecast, nothing at all from the hub, no
    # history, an unread heater. Every page and sub-page, each way.
    keep = (u.data, u.weather, u.history, u.page, u.sub, u.power_view, u.heater_view)
    for tag, data, wx in (("nothing", {}, None), ("no_forecast", SAMPLE, None),
                          ("empty_forecast", SAMPLE, {"ok": True, "meta": {}, "data": {}}),
                          ("max_charge", _extreme(1), WEATHER), ("max_discharge", _extreme(-1), WEATHER),
                          # a slow charge or drain: the longest "full in" / "left" wording
                          ("slow_charge", dict(SAMPLE, current=1.8, power_w=24, soc=57, residual_ah=57.0,
                                               stats={"time_to_full_h": 23.9}), WEATHER),
                          ("slow_discharge", dict(SAMPLE, current=-3.4, power_w=-44, soc=57, residual_ah=57.0,
                                                  stats={"time_to_empty_h": 16.8}), WEATHER)):
        u.data, u.weather, u.history = data, wx, None
        for i, name in enumerate(u.pages):
            u.page, u.sub = i, None
            shot("edge_%s_%s" % (tag, name))
        u.page = u.pages.index("power")
        for sub in ("battery", "lights"):
            u.sub = sub; shot("edge_%s_power_%s" % (tag, sub))
        u.sub = None
        for pv in ("now", "day"):
            u.power_view = pv; shot("edge_%s_power_%s" % (tag, pv))
        u.power_view = "flow"
        u.page = u.pages.index("heater")
        for hv in ("dial", "flow"):
            u.heater_view = hv; shot("edge_%s_heater_%s" % (tag, hv))
        u.sub = "schedule"; shot("edge_%s_schedule" % tag)
        u.page = u.pages.index("home")
        for sub in ("forecast", "timer"):
            u.sub = sub; shot("edge_%s_%s" % (tag, sub))
        u.sub = None
    u.data, u.weather, u.history, u.page, u.sub, u.power_view, u.heater_view = keep
    # the heater diagram, in its states, with the moving parts at one instant
    u.page = u.pages.index("heater"); u.heater_view = "flow"
    base = SAMPLE["heater"]
    for tag, extra in (("combi", {"mode_code": 3, "energy_code": 0, "mode": "Combi"}),
                       ("air_electric", {"mode_code": 4, "energy_code": 3}),
                       ("off", {"mode_code": 10, "on": False}),
                       ("unread", None)):
        h = dict(base, **extra) if extra else {}
        u.data = dict(SAMPLE, heater=h)
        u.adopt_heater(h) if h else None
        shot("heater_flow_" + tag)
        if u.animating():
            u.anim_draw(370)
            to_png(buf, 480, 320, os.path.join(out, "heater_flow_%s_moving.png" % tag))
    u.data = SAMPLE; u.heater_view = "dial"
    u.page = u.pages.index("power")
    u.power_view = "flow"
    u.render(); u.flow_dots(300)
    to_png(buf, 480, 320, os.path.join(out, "power_flow_dots.png"))
    u.page = u.pages.index("home"); u.sub = "forecast"; shot("forecast")
    u.data["gps"] = {"fix": True, "stale": False, "lat": 54.30420, "lon": -2.19790}
    shot("forecast_gps")
    u.data.pop("gps", None)
    cold = dict(WEATHER["data"], hourly=dict(WEATHER["data"]["hourly"],
                temperature_2m=[-12 + (h % 3) for h in range(48)]))
    u.weather = dict(WEATHER, data=cold); shot("forecast_frost"); u.weather = WEATHER
    u.sub = "timer"; shot("timer")
    u.kt_end = 1000 + 263; shot("timer_running")
    u.kt_end = None; u.kt_done = True; shot("timer_done"); u.kt_done = False
    u.page = u.pages.index("heater"); u.sub = "schedule"; shot("schedule"); u.sub = None
    u.saver = True
    shot("screensaver")
    u.saver = False
    # Lights, panic, and the alarm clock
    u.page = u.pages.index("power"); u.sub = "lights"
    for _ in u.wheel_steps():
        pass
    u.ambient = {"on": True, "h": 210, "s": 75, "bright": 60, "cycle": False, "warm": False}
    shot("lights")
    u.ambient = dict(u.ambient, cycle=True); shot("lights_cycle")
    # the Power page's other sub-page, and the Switches page
    u.sub = "battery"; shot("power_battery"); u.sub = "lights"
    u.page = u.pages.index("switches"); u.sub = None
    shot("switches")
    u.switches = [True, True, False, True, False, False]; shot("switches_some_on")
    u.switches = [False] * 6
    # the Tanks page: the sample levels, then the extremes and warnings
    u.page = u.pages.index("tanks")
    shot("tanks")
    u.tanks = {"fresh": 100, "grey": 0, "demo": True}; shot("tanks_full_empty")
    u.tanks = {"fresh": 8, "grey": 92, "demo": True}; shot("tanks_warnings")
    u.tanks = {"fresh": 30, "grey": 70}; shot("tanks_amber")
    u.tanks = {"fresh": 90, "grey": 33, "demo": True}
    # the Level page's G-force view, after a short drive: pulling away, a
    # right-hand bend, braking, and a pothole
    u.page = u.pages.index("level"); u.sub = "gforce"
    shot("gforce_parked")

    def _g(roll, pitch, mag=1.02):
        return {"ok": True, "roll": roll, "pitch": pitch, "axes": {"x": 0.0, "y": mag, "z": 0.0}}
    for _s in ([_g(0, a) for a in (2, 5, 8, 8, 6)] + [_g(r, 2) for r in (5, 10, 14, 12, 8)]
               + [_g(1, -a) for a in (5, 12, 19, 15)] + [_g(-6, -14, 1.25)]):
        u.level = _s
        u.gforce_sample(_s)
    shot("gforce_drive")
    for _s in [_g(-25, 30, 1.6)] * 3:          # off the scale: held at the edge
        u.level = _s
        u.gforce_sample(_s)
    shot("gforce_extreme")
    u.sub = None; u.level = None
    u.page = u.pages.index("power"); u.sub = "lights"
    u.ambient = dict(u.ambient, cycle=False)
    u.panic_arming = 1000 * 1000 - 500               # half-way through the hold
    shot("lights_holding"); u.panic_arming = None
    u.panic = True; shot("panic"); u.panic = False
    for st in ("arming", "armed", "tripped"):
        u.data = dict(SAMPLE, guard={"on": True, "state": st, "arming_s": 42})
        shot("lights_guard_" + st)
    u.data = SAMPLE
    u.page = u.pages.index("home"); u.sub = "timer"; u.clock_view = "alarm"
    u.alarm_clock = {"on": True, "at": "06:45", "days": "weekdays"}
    shot("alarm_clock")
    u.alarm_clock = {"on": True, "at": "06:45", "days": "weekdays", "warm": 30}
    u.data = dict(SAMPLE, wake={"at": "06:15"})
    shot("alarm_clock_warm")
    u.data = SAMPLE
    u.ac_ringing = True; shot("alarm_ringing"); u.ac_ringing = False
    u.clock_view = "timer"; u.sub = None
    # Level page, wheel heights; Drive page part ticked; and night mode
    u.page = u.pages.index("level")
    u.level_view = "wheels"
    u.data = dict(SAMPLE, level=dict(SAMPLE["level"], roll=-1.2, pitch=-0.9, off_by=1.5))
    shot("level_wheels")
    u.data = dict(SAMPLE, level=dict(SAMPLE["level"], roll=-3.5, pitch=-3.0, off_by=4.6))
    shot("level_wheels_steep")
    u.level_view = "bubble"
    u.data = dict(SAMPLE, mains_live=True)
    u.page = u.pages.index("drive")
    u.ticks = {"Roof vents shut", "Cupboards latched", "Windows shut"}
    shot("drive")
    u.data = dict(SAMPLE, mains_live=False,
                  heater=dict(SAMPLE["heater"], on=False))
    u.ticks = set(u.checklist)
    shot("drive_ready")
    u.night = True
    for name in ("home", "drive", "level"):
        u.page = u.pages.index(name)
        shot("night_" + name)
    u.night = False
    u.render()                                         # back to the day palette
    u.ticks = set()
    u.page = u.pages.index("power")
    u.link, u.link_note = "down", "Hub not answering"
    u.data = dict(SAMPLE, connected=False, alerts=[{"title": "Mains hook-up while driving"}])
    shot("power_offline_alert")

    # contact sheet
    ims = [Image.open(p) for p in shots]
    cols = 2
    rows = (len(ims) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 490 + 10, rows * 330 + 10), (40, 40, 40))
    for i, im in enumerate(ims):
        sheet.paste(im, (10 + (i % cols) * 490, 10 + (i // cols) * 330))
    sp = os.path.join(out, "all_pages.png")
    sheet.save(sp)
    print("wrote %d pages to %s" % (len(shots), out))
    print("\n".join(sorted(set(problems))) if problems
          else "layout check: no text runs out of its card or button")
    if fit_notes:
        print("buttons using the smallest font:", sorted(set(fit_notes)))
    print(sp)


if __name__ == "__main__":
    main()
