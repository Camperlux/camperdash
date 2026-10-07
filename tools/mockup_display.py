"""Design mock-ups of a more polished van display, rendered on the PC.

Drawn with the display's own pieces - gfx fonts and colours, and the smooth
shapes in display/aa.py - so nothing here is beyond what the device can draw.
It is a proposal for two pages, Home and Heater, to agree a look before every
page is reworked.

Run:  python tools/mockup_display.py      -> preview/mockup_*.png
"""

import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "display"))

from preview_display import FrameBuffer, install_framebuf, to_png, SAMPLE, WEATHER  # noqa

install_framebuf()
import gfx  # noqa: E402
from gfx import hexc, blend  # noqa: E402
from aa import AA  # noqa: E402

W, H = 480, 320
BG_TOP, BG_BOT = hexc("#15131a"), hexc("#0a090d")
CARD, CARD_HI = hexc("#1c1a22"), hexc("#2a2731")
TRACK = hexc("#2c2833")
TXT, MUTED, BRAND = gfx.TXT, gfx.MUTED, gfx.BRAND
FLAME, AMBER, RED, GREEN, BLUE = gfx.FLAME, gfx.AMBER, gfx.RED, gfx.GREEN, gfx.BLUE
EMBER = hexc("#ff6a3d")
BLACK = gfx.BLACK


class Canvas:
    def __init__(self):
        self.buf = bytearray(W * H * 2)
        self.fb = FrameBuffer(self.buf, W, H, FrameBuffer.RGB565)
        self.a = AA(self.buf, W, H, self.fb)
        self.f = gfx.Fonts(os.path.join(ROOT, "display", "fonts"))

    # ---- surfaces -------------------------------------------------------------

    def background(self):
        for y in range(H):
            self.fb.fill_rect(0, y, W, 1, blend(BG_TOP, BG_BOT, y / H))

    def card(self, x, y, w, h, r=16):
        a = self.a
        a.rrect(x, y + 3, w, h, r, BLACK, alpha=110)          # soft drop shadow
        a.rrect(x, y, w, h, r, CARD_HI)                        # 1 px lit top edge
        a.rrect(x, y + 1, w, h - 1, r, CARD)

    def pill(self, x, y, w, h, col, alpha=255):
        self.a.rrect(x, y, w, h, h // 2, col, alpha)

    # ---- icons: 2 px strokes in an 18 px box, drawn smooth --------------------

    def icon(self, name, x, y, col, s=18):
        a, k = self.a, s / 18.0

        def L(x0, y0, x1, y1, w=2.0):
            a.line(x + x0 * k, y + y0 * k, x + x1 * k, y + y1 * k, w * k, col)

        if name == "home":
            L(1, 9, 9, 2); L(9, 2, 17, 9); L(3, 8, 3, 17); L(15, 8, 15, 17); L(3, 17, 15, 17)
            L(7, 17, 7, 12); L(11, 17, 11, 12); L(7, 12, 11, 12)
        elif name == "check":
            L(2, 2, 16, 2); L(16, 2, 16, 16); L(16, 16, 2, 16); L(2, 16, 2, 2)
            L(5, 9, 8, 12); L(8, 12, 13, 6)
        elif name == "bolt":
            L(11, 1, 4.5, 10); L(4.5, 10, 12.5, 9); L(12.5, 9, 6.5, 17.5)
        elif name == "flame":
            L(9, 1, 14.5, 9.5); L(14.5, 9.5, 14.8, 12.5); L(9, 1, 5.5, 7); L(5.5, 7, 3.4, 11)
            a.arc(x + 9 * k, y + 12 * k, 4.6 * k, 6.6 * k, 1.35, 5.0, col)
            a.disc(x + 9 * k, y + 13.6 * k, 2.4 * k, col)
        elif name == "drop":
            a.disc(x + 9 * k, y + 12 * k, 5.5 * k, col)
            a.poly(((x + 9 * k, y + 1 * k), (x + 14.1 * k, y + 10 * k), (x + 3.9 * k, y + 10 * k)), col)
        elif name == "level":
            a.ring(x + 9 * k, y + 9 * k, 6.8 * k, 8.8 * k, col)
            a.disc(x + 11.5 * k, y + 7 * k, 2.6 * k, col)
        elif name == "battery":
            L(1, 5, 15, 5); L(15, 5, 15, 14); L(15, 14, 1, 14); L(1, 14, 1, 5)
            L(17, 8, 17, 11, 2.4)
            a.rrect(x + 3.5 * k, y + 7.5 * k, 7 * k, 4 * k, 1, col)
        elif name == "sun":
            a.disc(x + 9 * k, y + 9 * k, 3.6 * k, col)
            for i in range(8):
                t = i * math.pi / 4
                L(9 + math.sin(t) * 6.2, 9 - math.cos(t) * 6.2,
                  9 + math.sin(t) * 8.3, 9 - math.cos(t) * 8.3, 1.6)
        elif name == "wifi":
            for r in (3.5, 7.5, 11.5):
                a.arc(x + 9 * k, y + 15 * k, (r - 1.1) * k, (r + 1.1) * k,
                      -0.8, 0.8, col, caps=True)
            a.disc(x + 9 * k, y + 15.5 * k, 1.6 * k, col)
        elif name == "clock":
            a.ring(x + 9 * k, y + 9 * k, 6.9 * k, 8.7 * k, col)
            L(9, 9, 9, 4.5, 1.8); L(9, 9, 12, 10.5, 1.8)
        elif name == "power":
            a.arc(x + 9 * k, y + 10 * k, 5.9 * k, 7.7 * k, 0.6, 2 * math.pi - 0.6, col)
            L(9, 1.5, 9, 8.5, 2.2)
        elif name == "refresh":
            a.arc(x + 9 * k, y + 9 * k, 5.9 * k, 7.7 * k, 0.9, 2 * math.pi - 0.3, col)
            a.poly(((x + 14.5 * k, y + 1 * k), (x + 16.5 * k, y + 7.5 * k), (x + 10 * k, y + 6 * k)), col)
        elif name == "minus":
            L(3, 9, 15, 9, 2.6)
        elif name == "plus":
            L(3, 9, 15, 9, 2.6); L(9, 3, 9, 15, 2.6)

    # ---- chrome -----------------------------------------------------------------

    def header(self, title, time_txt="20:14"):
        f, fb = self.f, self.fb
        f.mdb.text(fb, title, 18, 12, TXT, BG_TOP)
        x = W - 18
        x -= f.mdb.text(fb, time_txt, x, 12, TXT, BG_TOP, 2) + 14
        self.icon("sun", x - 18, 11, MUTED)
        self.icon("wifi", x - 46, 9, GREEN)

    def tabs(self, active):
        a, f, fb = self.a, self.f, self.fb
        y0 = 276
        fb.fill_rect(0, y0, W, H - y0, blend(BG_BOT, CARD, 0.7))
        fb.fill_rect(0, y0, W, 1, blend(CARD, CARD_HI, 0.6))
        items = (("Home", "home"), ("Drive", "check"), ("Power", "bolt"), ("Heater", "flame"),
                 ("Level", "level"), ("Battery", "battery"))
        tw = W // len(items)
        for i, (label, ic) in enumerate(items):
            cx = i * tw + tw // 2
            on = label == active
            back = blend(BG_BOT, CARD, 0.7)
            if on:
                back = blend(back, BRAND, 0.16)
                a.rrect(cx - 34, y0 + 5, 68, 38, 12, back)
            col = BRAND if on else MUTED
            self.icon(ic, cx - 9, y0 + 7, col)
            f.sm.text(fb, label, cx, y0 + 26, col, back, 1)

    def save(self, name):
        out = os.path.join(ROOT, "preview")
        os.makedirs(out, exist_ok=True)
        p = os.path.join(out, "mockup_%s.png" % name)
        to_png(self.buf, W, H, p)
        return p


# ---- Home ----------------------------------------------------------------------

def home(c):
    a, f, fb = c.a, c.f, c.fb
    c.background()
    c.header("Friday 25 September")
    # the clock: a lit face with a rim, smooth ticks and hands
    cx, cy, R = 128, 160, 106
    a.disc(cx, cy + 3, R + 4, BLACK, alpha=110)
    a.disc(cx, cy, R + 4, CARD_HI)
    a.disc(cx, cy, R + 2, blend(CARD, BG_BOT, 0.35))
    a.disc(cx, cy, R - 6, CARD)
    for i in range(60):
        t = i * math.pi / 30
        big = i % 5 == 0
        r0 = R - (15 if big else 9)
        a.line(cx + math.sin(t) * r0, cy - math.cos(t) * r0,
               cx + math.sin(t) * (R - 5), cy - math.cos(t) * (R - 5),
               2.6 if big else 1.2, TXT if big else blend(CARD, MUTED, 0.7))
    for n, t in ((12, 0), (3, math.pi / 2), (6, math.pi), (9, 1.5 * math.pi)):
        f.md.text(fb, str(n), int(cx + math.sin(t) * (R - 32)),
                  int(cy - math.cos(t) * (R - 32)) - 10, MUTED, CARD, 1)
    hh, mm, ss = 20, 14, 38
    for ang, ln, wd, col in (((hh % 12 + mm / 60) * math.pi / 6, R * 0.52, 7, TXT),
                             ((mm + ss / 60) * math.pi / 30, R * 0.78, 5, TXT)):
        a.line(cx + 2, cy + 3, cx + 2 + math.sin(ang) * ln, cy + 3 - math.cos(ang) * ln,
               wd, BLACK, alpha=90)                       # hand shadow
        a.line(cx - math.sin(ang) * 12, cy + math.cos(ang) * 12,
               cx + math.sin(ang) * ln, cy - math.cos(ang) * ln, wd, col)
    sa = ss * math.pi / 30
    a.line(cx - math.sin(sa) * 18, cy + math.cos(sa) * 18,
           cx + math.sin(sa) * R * 0.86, cy - math.cos(sa) * R * 0.86, 2, BRAND)
    a.disc(cx, cy, 6, BRAND)
    a.disc(cx, cy, 2.5, CARD)

    # weather
    x, y, w, h = 252, 48, 214, 122
    c.card(x, y, w, h)
    f.sm.text(fb, "WEATHER", x + 16, y + 12, MUTED, CARD)
    f.sm.text(fb, "Hawes", x + w - 16, y + 12, MUTED, CARD, 2)
    ix, iy = x + 50, y + 58
    for dx, dy, r in ((-13, 3, 13), (2, -6, 17), (17, 4, 12)):
        a.disc(ix + dx, iy + dy, r, hexc("#c9c5d1"))
    a.rrect(ix - 26, iy + 2, 55, 15, 7, hexc("#c9c5d1"))
    for dx in (-12, 0, 12):
        a.line(ix + dx, iy + 24, ix + dx - 4, iy + 33, 2.4, BLUE)
    f.xl.text(fb, "14°", x + 98, y + 34, TXT, CARD)
    f.md.text(fb, "Light rain", x + 98, y + 80, MUTED, CARD)
    f.sm.text(fb, "H 15°  L 11°  ·  8 km/h", x + 16, y + h - 26, MUTED, CARD)

    # battery
    y2 = y + h + 10
    h2 = 272 - y2 - 6
    c.card(x, y2, w, h2)
    bx, by, bw, bh = x + 16, y2 + 18, 92, 44
    a.rrect(bx, by, bw, bh, 9, blend(CARD, MUTED, 0.55))
    a.rrect(bx + 2, by + 2, bw - 4, bh - 4, 7, CARD)
    a.rrect(bx + bw + 1, by + bh // 2 - 8, 6, 16, 3, blend(CARD, MUTED, 0.55))
    fill = int((bw - 10) * 0.78)
    for yy in range(bh - 10):                              # vertical sheen on the fill
        fb.fill_rect(bx + 5, by + 5 + yy, fill, 1,
                     blend(hexc("#5fd97a"), hexc("#2f9e4a"), yy / (bh - 10)))
    c.icon("bolt", bx + 32, by + 8, TXT, 28)
    f.lg.text(fb, "78%", x + 124, y2 + 16, TXT, CARD)
    c.pill(x + 124, y2 + 50, 76, 20, blend(CARD, GREEN, 0.2))
    f.sm.text(fb, "Charging", x + 162, y2 + 52, GREEN, blend(CARD, GREEN, 0.2), 1)
    f.sm.text(fb, "Full in 3 h 24 m", x + 16, y2 + h2 - 22, MUTED, CARD)
    c.tabs("Home")
    return c.save("home")


# ---- Heater ---------------------------------------------------------------------

def heater(c):
    a, f, fb = c.a, c.f, c.fb
    c.background()
    c.header("Heater")
    cx, cy, R, th = 152, 152, 104, 16
    r0 = R - th
    start, end = -math.radians(135), math.radians(135)
    target, cabin = 20, 17
    ta = start + (target - 5) / 30 * (end - start)
    a.arc(cx, cy, r0, R, start, end, TRACK)
    a.arc(cx, cy, r0 - 6, R + 6, start, ta, FLAME, alpha=38)          # glow
    n = 28
    for i in range(n):                                                 # warm gradient
        s0 = start + (ta - start) * i / n
        s1 = start + (ta - start) * (i + 1.08) / n
        col = blend(AMBER, EMBER, i / (n - 1))
        a.arc(cx, cy, r0, R, s0, min(s1, ta), col, caps=(i == 0))
    kx, ky = cx + math.sin(ta) * (r0 + th / 2), cy - math.cos(ta) * (r0 + th / 2)
    a.disc(kx + 1, ky + 3, 14, BLACK, alpha=120)
    a.disc(kx, ky, 14, TXT)
    a.disc(kx, ky, 8, EMBER)
    ca = start + (cabin - 5) / 30 * (end - start)                     # cabin marker
    a.line(cx + math.sin(ca) * (r0 - 14), cy - math.cos(ca) * (r0 - 14),
           cx + math.sin(ca) * (r0 - 5), cy - math.cos(ca) * (r0 - 5), 3, TXT)

    f.sm.text(fb, "HEATING TO", cx, cy - 60, MUTED, BG_TOP, 1)
    tw = f.xxl.width("20")
    f.xxl.text(fb, "20", cx - 8, cy - 42, TXT, blend(BG_TOP, BG_BOT, cy / H), 1)
    f.lg.text(fb, "°", cx - 8 + tw // 2 + 2, cy - 40, MUTED, blend(BG_TOP, BG_BOT, cy / H))
    pb = blend(blend(BG_TOP, BG_BOT, 0.6), FLAME, 0.18)
    c.pill(cx - 64, cy + 36, 128, 26, pb)
    c.icon("flame", cx - 54, cy + 41, FLAME, 16)
    f.sm.text(fb, "Heating · Air", cx + 10, cy + 41, FLAME, pb, 1)
    f.sm.text(fb, "Cabin 17°", cx, cy + 70, MUTED, blend(BG_TOP, BG_BOT, 0.7), 1)

    for bx, ic in ((cx - R + 4, "minus"), (cx + R - 4, "plus")):       # round - and +
        by = cy + R - 8
        a.disc(bx, by + 2, 22, BLACK, alpha=100)
        a.disc(bx, by, 22, CARD_HI)
        a.disc(bx, by, 21, CARD)
        c.icon(ic, bx - 9, by - 9, TXT)
    c.pill(100, 8, 88, 26, CARD)
    c.icon("clock", 110, 13, MUTED, 16)
    f.sm.text(fb, "Timers", 132, 12, TXT, CARD)

    # mode buttons
    x, w, bh = 300, 168, 40
    rows = (("flame", "Air heat", True), ("flame", "Air + water", False),
            ("drop", "Water heat", False))
    for i, (ic, label, on) in enumerate(rows):
        y = 46 + i * (bh + 7)
        if on:
            a.rrect(x, y + 3, w, bh, 14, BLACK, alpha=110)
            a.rrect(x, y, w, bh, 14, FLAME)
            a.rrect(x, y + bh // 2, w, bh // 2, 14, blend(FLAME, EMBER, 0.6))
            a.rrect(x, y + bh // 2 - 8, w, 16, 0, blend(FLAME, EMBER, 0.3))
            col, back = BLACK, blend(FLAME, EMBER, 0.3)
        else:
            c.card(x, y, w, bh, 14)
            col, back = TXT, CARD
        c.icon(ic, x + 14, y + 11, col)
        if label == "Air + water":
            c.icon("drop", x + 30, y + 12, col, 16)
        tx = x + (52 if label == "Air + water" else 42)
        fnt = f.mdb if f.mdb.width(label) <= x + w - 12 - tx else f.md
        fnt.text(fb, label, tx, y + 10, col, back)
    y = 46 + 3 * (bh + 7)
    c.card(x, y, w - 52, bh, 14)
    c.icon("drop", x + 14, y + 11, BLUE)
    f.md.text(fb, "60°C", x + 38, y + 10, TXT, CARD)
    c.card(x + w - 44, y, 44, bh, 14)
    c.icon("refresh", x + w - 31, y + 11, MUTED)
    y += bh + 7
    rb = blend(CARD, RED, 0.28)
    a.rrect(x, y + 3, w, bh - 4, 14, BLACK, alpha=100)
    a.rrect(x, y, w, bh - 4, 14, rb)
    c.icon("power", x + 56, y + 9, TXT)
    f.mdb.text(fb, "Off", x + 82, y + 8, TXT, rb)
    c.tabs("Heater")
    return c.save("heater")


if __name__ == "__main__":
    for fn in (home, heater):
        print(fn(Canvas()))
