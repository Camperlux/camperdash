"""Make the display's screensaver logo from the Camperlux badge artwork.

The source (Camperlux/3D Logo CamperLux.jpg, 1008 x 1024) is a round badge on
a white background with a drop shadow. The badge is cut out with a circular
mask - found from its dark metal rim, not hard-coded - drawn anti-aliased onto
the display's background colour, and written as raw RGB565, byte-swapped for
the panel exactly as gfx.rgb() does, so the display can blit it with no work.

File format (display/logo.bin): b"IMG1", u16 width, u16 height, then pixels.

Run:  python tools/make_logo.py
"""

import os
import struct

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(os.path.dirname(ROOT), "Camperlux", "3D Logo CamperLux.jpg")
OUT = os.path.join(ROOT, "display", "logo.bin")
PREVIEW = os.path.join(ROOT, "preview", "logo_preview.png")
WEB = os.path.join(ROOT, "static", "badge.jpg")
SIZE = 272                   # diameter on the 480 x 320 screen
BG = (0x0F, 0x0E, 0x12)      # gfx.BG, the dashboard's background


def main():
    im = Image.open(SRC).convert("RGB")
    # The badge's rim is dark metal; the background and shadow are pale. The
    # bounding box of the dark pixels is the circle.
    grey = im.convert("L").point(lambda v: 255 if v < 110 else 0)
    x0, y0, x1, y1 = grey.getbbox()
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    r = min(x1 - x0, y1 - y0) / 2 - 2          # just inside the rim's edge
    badge = im.crop((int(cx - r), int(cy - r), int(cx + r), int(cy + r)))

    # anti-aliased circular mask, drawn 4x large and scaled down
    big = SIZE * 4
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, big - 1, big - 1), fill=255)
    mask = mask.resize((SIZE, SIZE), Image.LANCZOS)

    badge = badge.resize((SIZE, SIZE), Image.LANCZOS)
    out = Image.new("RGB", (SIZE, SIZE), BG)
    out.paste(badge, (0, 0), mask)

    raw = out.tobytes()
    data = bytearray()
    for i in range(0, len(raw), 3):
        r_, g, b = raw[i], raw[i + 1], raw[i + 2]
        c = ((r_ & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        data += bytes((c >> 8, c & 255))       # big-endian: the panel's order
    with open(OUT, "wb") as f:
        f.write(b"IMG1" + struct.pack("<HH", SIZE, SIZE) + data)
    os.makedirs(os.path.dirname(PREVIEW), exist_ok=True)
    out.save(PREVIEW)
    # and for the hub's web app: the same badge, larger, as a JPEG on the
    # app's background colour (the logo page, past either end of the tabs)
    wb = 480
    wmask = Image.new("L", (wb * 4, wb * 4), 0)
    ImageDraw.Draw(wmask).ellipse((0, 0, wb * 4 - 1, wb * 4 - 1), fill=255)
    wmask = wmask.resize((wb, wb), Image.LANCZOS)
    web = Image.new("RGB", (wb, wb), BG)
    web.paste(im.crop((int(cx - r), int(cy - r), int(cx + r), int(cy + r))).resize((wb, wb), Image.LANCZOS),
              (0, 0), wmask)
    web.save(WEB, quality=86, optimize=True, progressive=True)
    print("web badge:", WEB, os.path.getsize(WEB), "bytes")
    print("badge found at (%d, %d) r=%d in the source" % (cx, cy, r))
    print("wrote %s, %d x %d, %d bytes" % (OUT, SIZE, SIZE, 8 + len(data)))


if __name__ == "__main__":
    main()
