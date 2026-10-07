# Drawing helpers: colours, anti-aliased text, rounded boxes, bars.
#
# Only framebuf primitives that run in C are used (fill_rect, ellipse, line,
# blit), so drawing a whole page stays in the tens of milliseconds. The same
# code runs on the PC under tools/preview_display.py to render screenshots.

import framebuf
import struct


def rgb(r, g, b):
    """An RGB565 colour, byte-swapped for the panel (see st7796.py)."""
    c = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    return ((c & 255) << 8) | (c >> 8)


def hexc(s):
    return rgb(int(s[1:3], 16), int(s[3:5], 16), int(s[5:7], 16))


def _unswap(c):
    c = ((c & 255) << 8) | (c >> 8)
    return ((c >> 8) & 0xF8, (c >> 3) & 0xFC, (c << 3) & 0xF8)


def blend(a, b, t):
    """Mix two panel colours; t=0 gives a, t=1 gives b."""
    ra, ga, ba = _unswap(a)
    rb, gb, bb = _unswap(b)
    return rgb(int(ra + (rb - ra) * t), int(ga + (gb - ga) * t),
               int(ba + (bb - ba) * t))


# The web dashboard's palette (static/app.css), so the two look like one product.
BG = hexc("#0f0e12")
PANEL = hexc("#1c1a22")      # cards
PANEL2 = hexc("#27242e")     # buttons, wells, tracks
LINE = hexc("#34303c")
CARD_HI = hexc("#2c2934")    # the lit top edge of a card
BG_TOP = hexc("#15131a")     # the page background runs from this ...
BG_BOT = hexc("#0a090d")     # ... down to this
EMBER = hexc("#ff6a3d")      # the hot end of the heater dial
TXT = hexc("#efe9e0")
MUTED = hexc("#9a8f9e")
GREEN = hexc("#3fb950")
AMBER = hexc("#d29922")
RED = hexc("#f85149")
BLUE = hexc("#58a6ff")
CYAN = hexc("#39d3c3")
BRAND = hexc("#d3a94a")
FLAME = hexc("#ff9d3c")
BLACK = rgb(0, 0, 0)


class Font:
    """A font from tools/make_fonts.py. Loaded whole: the largest is 50 KB and
    the board has 8 MB of PSRAM."""

    def __init__(self, path):
        with open(path, "rb") as f:
            # a bytearray, not bytes: framebuf will only wrap a writable buffer
            blob = bytearray(f.read())
        if blob[:4] != b"FNT1":
            raise ValueError("not a font: " + path)
        self.height, n = struct.unpack_from("<HH", blob, 4)
        data = memoryview(blob)[8 + n * 8:]
        self._g = {}
        for i in range(n):
            cp, w, off = struct.unpack_from("<HHI", blob, 8 + i * 8)
            stride = (w + 1) // 2
            buf = data[off:off + stride * self.height]
            # GS4 rows are padded to whole bytes, so the buffer is 2*stride wide;
            # the padding pixel is 0 and is skipped by the blit's key.
            fb = framebuf.FrameBuffer(buf, stride * 2, self.height,
                                      framebuf.GS4_HMSB)
            self._g[cp] = (w, fb)
        self._fallback = self._g.get(ord("?"))
        self._pal = {}

    def _palette(self, fg, bg):
        key = (fg, bg)
        p = self._pal.get(key)
        if p is None:
            if len(self._pal) > 32:
                self._pal.clear()
            buf = bytearray(32)
            p = framebuf.FrameBuffer(buf, 16, 1, framebuf.RGB565)
            for i in range(16):
                p.pixel(i, 0, blend(bg, fg, i / 15))
            self._pal[key] = p
        return p

    def width(self, s):
        g = self._g
        fb = self._fallback
        w = 0
        for ch in s:
            w += g.get(ord(ch), fb)[0]
        return w

    def text(self, fbuf, s, x, y, fg, bg, align=0):
        """Draw s with its top-left at (x, y). align: 0 left, 1 centre, 2
        right - x is then the centre or the right edge. Returns the width.

        bg must be the colour already underneath: the edges are blended
        towards it, which is what makes the text smooth."""
        w = self.width(s)
        if align == 1:
            x -= w // 2
        elif align == 2:
            x -= w
        pal = self._palette(fg, bg)
        g = self._g
        fb = self._fallback
        # framebuf compares the key against the colour after the palette has
        # been applied (checked on the device), so the transparent value is
        # palette entry 0 - the background - not index 0.
        for ch in s:
            cw, gb = g.get(ord(ch), fb)
            fbuf.blit(gb, x, y, bg, pal)
            x += cw
        return w


class Fonts:
    def __init__(self, folder="fonts"):
        for name in ("sm", "md", "mdb", "lg", "xl", "xxl"):
            setattr(self, name, Font(folder + "/" + name + ".fnt"))


# Smooth shapes: set to an aa.AA for the frame buffer and the helpers below
# draw anti-aliased; left as None (a bare framebuf) they draw as before.
AA = None


def rrect(fb, x, y, w, h, r, c):
    """Filled rectangle with rounded corners."""
    if AA is not None and r > 0:
        AA.rrect(x, y, w, h, r, c)
        return
    if r <= 0:
        fb.fill_rect(x, y, w, h, c)
        return
    fb.fill_rect(x + r, y, w - 2 * r, h, c)
    fb.fill_rect(x, y + r, r, h - 2 * r, c)
    fb.fill_rect(x + w - r, y + r, r, h - 2 * r, c)
    # ellipse quadrant masks: 1 = top-right, 2 = top-left, 4 = bottom-left,
    # 8 = bottom-right
    fb.ellipse(x + w - r - 1, y + r, r, r, c, True, 1)
    fb.ellipse(x + r, y + r, r, r, c, True, 2)
    fb.ellipse(x + r, y + h - r - 1, r, r, c, True, 4)
    fb.ellipse(x + w - r - 1, y + h - r - 1, r, r, c, True, 8)


def rframe(fb, x, y, w, h, r, c, inner):
    """Rounded outline, drawn as two filled shapes so the corners match."""
    rrect(fb, x, y, w, h, r, c)
    rrect(fb, x + 1, y + 1, w - 2, h - 2, max(0, r - 1), inner)


def bar(fb, x, y, w, h, frac, c, back=None):
    """Horizontal level bar, 0.0..1.0, with rounded ends."""
    back = LINE if back is None else back
    r = h // 2
    rrect(fb, x, y, w, h, r, back)
    frac = 0.0 if frac is None or frac < 0 else 1.0 if frac > 1 else frac
    fw = int(w * frac)
    if fw >= h:
        rrect(fb, x, y, fw, h, r, c)
    elif fw > 0:
        dot(fb, x + r, y + r, r, c)


def dot(fb, x, y, r, c):
    if AA is not None:
        AA.disc(x, y, r, c)
    else:
        fb.ellipse(x, y, r, r, c, True)


def thick_line(fb, x0, y0, x1, y1, c, t=2):
    if AA is not None:
        AA.line(x0, y0, x1, y1, t, c)
        return
    for i in range(-(t // 2), t - t // 2):
        if abs(x1 - x0) > abs(y1 - y0):
            fb.line(x0, y0 + i, x1, y1 + i, c)
        else:
            fb.line(x0 + i, y0, x1 + i, y1, c)
