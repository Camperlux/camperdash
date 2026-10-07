"""Render the display's fonts to compact bitmap files.

MicroPython's framebuf has one built-in font, 8x8 pixels and with no smoothing,
which is unreadable from across a van. So the fonts are rendered here, on the
PC, from Inter (tools/fonts, SIL Open Font Licence - see Inter-LICENSE.txt), and
the display only ever blits them. Any character Inter lacks comes from DejaVu
Sans (shipped with matplotlib).

Digits are all given the same width - "tabular" figures - so a clock or a
temperature does not shuffle sideways as it changes. Inter can do this itself,
but only through an OpenType feature Pillow cannot switch on without extra
libraries, so the glyphs are centred in equal cells here instead.

Each glyph is stored at 4 bits per pixel: 16 levels of coverage, so edges are
smoothed. On the device a glyph is blitted through a 16-entry palette that
blends from the background colour to the text colour, which gives anti-aliased
text for the cost of one framebuf.blit per character.

File format (.fnt, all little-endian):
    b"FNT1"  u16 height  u16 count
    count x (u16 codepoint, u16 width, u32 offset into the bitmap data)
    bitmap data: each glyph is height rows of ceil(width/2) bytes, GS4_HMSB

Every glyph is a full cell: its advance width by the font's height, with the
side bearings already in it. The height is cropped to the ink of the glyphs
the font actually holds, so a digits-only font has no empty space for
descenders, and text can be positioned by its visible top edge.

Run:  python tools/make_fonts.py
"""

import os
import struct

import matplotlib
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "display", "fonts")
TTF = os.path.join(os.path.dirname(matplotlib.__file__), "mpl-data", "fonts", "ttf")
INTER = os.path.join(ROOT, "tools", "fonts")
DIGITS = "0123456789"

ASCII = "".join(chr(c) for c in range(32, 127))
EXTRA = "°·–—↑↓←→±…↻"   # ° · – — ↑ ↓ ← → ± … ↻
NUMERIC = "0123456789.,-+%:/ °–"

# name: (Inter file, DejaVu fallback, pixel size, characters). Inter is
# narrower than DejaVu at the same size, so labels that were tight now fit.
FONTS = {
    "sm": ("Inter-Regular.ttf", "DejaVuSans.ttf", 15, ASCII + EXTRA),
    "md": ("Inter-Regular.ttf", "DejaVuSans.ttf", 18, ASCII + EXTRA),
    "mdb": ("Inter-SemiBold.ttf", "DejaVuSans-Bold.ttf", 18, ASCII + EXTRA),
    "lg": ("Inter-Bold.ttf", "DejaVuSans-Bold.ttf", 29, ASCII + EXTRA),
    "xl": ("Inter-Bold.ttf", "DejaVuSans-Bold.ttf", 46, NUMERIC + "VAWhmCkd"),
    "xxl": ("Inter-Bold.ttf", "DejaVuSans-Bold.ttf", 80, NUMERIC),
}


def _has(font, ch):
    """Does the font really have this character, rather than its 'missing'
    box? Compared against a character no font here has."""
    try:
        return font.getmask(ch).getbbox() != font.getmask("￿").getbbox() or ch == " "
    except Exception:
        return False


def render(ttf, fallback, size, chars):
    font = ImageFont.truetype(os.path.join(INTER, ttf), size)
    alt = ImageFont.truetype(os.path.join(TTF, fallback), size)
    ascent, descent = font.getmetrics()
    full_h = ascent + descent
    digit_w = max(int(round(font.getlength(d))) for d in DIGITS)
    cells = []
    for ch in chars:
        f = font if _has(font, ch) else alt
        w = max(1, int(round(f.getlength(ch))))
        x = 0
        if ch in DIGITS:                     # tabular: every digit the same width
            x = (digit_w - w) // 2
            w = digit_w
        img = Image.new("L", (w, full_h), 0)
        ImageDraw.Draw(img).text((x, ascent), ch, font=f, fill=255, anchor="ls")
        cells.append((ch, img))
    # crop every cell to the same rows: the union of the ink over all glyphs
    top, bottom = full_h, 0
    for ch, img in cells:
        bb = img.getbbox()
        if bb:
            top = min(top, bb[1])
            bottom = max(bottom, bb[3])
    return [(ch, img.crop((0, top, img.width, bottom))) for ch, img in cells], bottom - top


def pack(img):
    w, h = img.size
    px = img.load()
    stride = (w + 1) // 2
    out = bytearray(stride * h)
    for y in range(h):
        for x in range(w):
            v = px[x, y] >> 4                # 0..15
            i = y * stride + x // 2
            # GS4_HMSB: the left pixel is the high nibble
            out[i] |= (v << 4) if x % 2 == 0 else v
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, (ttf, fallback, size, chars) in FONTS.items():
        cells, h = render(ttf, fallback, size, sorted(set(chars)))
        index = b""
        data = bytearray()
        for ch, img in cells:
            index += struct.pack("<HHI", ord(ch), img.width, len(data))
            data += pack(img)
        blob = b"FNT1" + struct.pack("<HH", h, len(cells)) + index + data
        path = os.path.join(OUT, name + ".fnt")
        with open(path, "wb") as f:
            f.write(blob)
        print("%-4s %-22s %3dpx  height %3d  %3d glyphs  %6d bytes"
              % (name, ttf, size, h, len(cells), len(blob)))


if __name__ == "__main__":
    main()
