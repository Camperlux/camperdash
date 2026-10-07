"""Draw the README's overview pictures from the real photos and screens.

    python tools/make_overview.py

Writes, to docs/images/:
  overview.png      the whole system: the hub in the middle, the Bluetooth
                    equipment it talks to, what is wired to it, the screens
  every-screen.png  one page (the heater) on the display, a phone and the
                    van's own head unit
  features.png      the display's main pages, labelled

Inputs are files already in the repository: docs/photos/, docs/screenshots/
(display renders come from tools/preview_display.py) and the Inter typeface in
tools/fonts/. Run it again after replacing any of those.
"""
import math
import os

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = lambda *a: os.path.join(ROOT, *a)
OUT = P("docs", "images")

NIGHT = (18, 19, 24)
PANEL = (28, 29, 36)
PANEL2 = (38, 39, 48)
LINE = (58, 60, 72)
INK = (236, 235, 230)
MUTED = (154, 155, 166)
AMBER = (226, 172, 69)
BLUE = (88, 160, 230)
GREEN = (63, 179, 106)
RED = (214, 92, 70)


def font(size, weight="Regular"):
    return ImageFont.truetype(P("tools", "fonts", "Inter-%s.ttf" % weight), size)


def rounded(im, radius):
    """im with rounded corners, over the night background."""
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, im.width - 1, im.height - 1), radius, fill=255)
    out = Image.new("RGB", im.size, NIGHT)
    out.paste(im, (0, 0), mask)
    return out


def cover(path, w, h):
    """The image cropped to fill w x h, centred."""
    im = Image.open(path).convert("RGB")
    s = max(w / im.width, h / im.height)
    im = im.resize((max(w, round(im.width * s)), max(h, round(im.height * s))), Image.LANCZOS)
    x, y = (im.width - w) // 2, (im.height - h) // 2
    return im.crop((x, y, x + w, y + h))


def fit(path, w, h):
    """The whole image scaled to fit inside w x h."""
    im = Image.open(path).convert("RGB")
    s = min(w / im.width, h / im.height)
    return im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)


def text_c(d, xy, s, f, fill):
    w = d.textlength(s, font=f)
    d.text((xy[0] - w / 2, xy[1]), s, font=f, fill=fill)


def arrow(d, a, b, col, width=4, both=False, dash=False):
    """A line a -> b with an arrowhead (both ends if both)."""
    if dash:
        n = int(math.dist(a, b) // 14)
        for i in range(n):
            if i % 2 == 0:
                p = (a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n)
                q = (a[0] + (b[0] - a[0]) * (i + 1) / n, a[1] + (b[1] - a[1]) * (i + 1) / n)
                d.line((p, q), fill=col, width=width)
    else:
        d.line((a, b), fill=col, width=width)
    ends = [(a, b)] + ([(b, a)] if both else [])
    for frm, to in ends:
        ang = math.atan2(to[1] - frm[1], to[0] - frm[0])
        L = 16
        pts = [to, (to[0] - L * math.cos(ang - 0.45), to[1] - L * math.sin(ang - 0.45)),
               (to[0] - L * math.cos(ang + 0.45), to[1] - L * math.sin(ang + 0.45))]
        d.polygon(pts, fill=col)


def pill(d, x, y, s, f, fg, bg):
    w = d.textlength(s, font=f) + 26
    d.rounded_rectangle((x, y, x + w, y + 34), 17, fill=bg)
    d.text((x + 13, y + 6), s, font=f, fill=fg)
    return w


# ---- icons, drawn so nothing else needs shipping --------------------------------
def icon(d, kind, cx, cy, col):
    r = 30
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=PANEL2)
    if kind == "battery":
        d.rounded_rectangle((cx - 16, cy - 10, cx + 12, cy + 10), 3, outline=col, width=3)
        d.rectangle((cx + 13, cy - 4, cx + 17, cy + 4), fill=col)
        d.rectangle((cx - 12, cy - 6, cx + 2, cy + 6), fill=col)
    elif kind == "dcdc":
        d.polygon([(cx + 2, cy - 18), (cx - 10, cy + 2), (cx, cy + 2), (cx - 4, cy + 18), (cx + 10, cy - 4), (cx, cy - 4)], fill=col)
    elif kind == "mains":
        d.rounded_rectangle((cx - 12, cy - 6, cx + 12, cy + 12), 4, outline=col, width=3)
        d.line((cx - 5, cy - 16, cx - 5, cy - 6), fill=col, width=4)
        d.line((cx + 5, cy - 16, cx + 5, cy - 6), fill=col, width=4)
    elif kind == "heater":
        d.polygon([(cx, cy - 20), (cx + 13, cy + 2), (cx + 9, cy + 16), (cx - 9, cy + 16), (cx - 13, cy + 2)], fill=col)
        d.polygon([(cx, cy - 2), (cx + 6, cy + 8), (cx + 4, cy + 14), (cx - 4, cy + 14), (cx - 6, cy + 8)], fill=PANEL2)
    elif kind == "cloud":
        for ox, oy, rr in ((-9, 3, 10), (2, -4, 12), (12, 4, 9)):
            d.ellipse((cx + ox - rr, cy + oy - rr, cx + ox + rr, cy + oy + rr), fill=col)
        d.rectangle((cx - 14, cy + 3, cx + 16, cy + 13), fill=col)


def overview():
    W, H = 1700, 1000
    im = Image.new("RGB", (W, H), NIGHT)
    d = ImageDraw.Draw(im)
    f_head, f_name, f_sub = font(22, "SemiBold"), font(26, "Bold"), font(19)
    f_big, f_tag = font(46, "Bold"), font(18, "SemiBold")

    # -- left: the Bluetooth equipment
    lx, lw, ch = 40, 400, 130
    d.text((lx, 40), "BLUETOOTH  ·  read, and the heater controlled", font=f_head, fill=BLUE)
    devs = [("battery", "Fogstar battery", "BMS: charge, cells, faults", GREEN),
            ("dcdc", "Renogy DC-DC", "Alternator and solar", AMBER),
            ("mains", "Victron charger", "Mains hook-up", BLUE),
            ("heater", "JP diesel heater", "Air and hot water", RED)]
    dev_y = []
    for k, (ic, name, sub, col) in enumerate(devs):
        y = 90 + k * (ch + 28)
        d.rounded_rectangle((lx, y, lx + lw, y + ch), 18, fill=PANEL)
        icon(d, ic, lx + 58, y + ch // 2, col)
        d.text((lx + 108, y + 34), name, font=f_name, fill=INK)
        d.text((lx + 108, y + 72), sub, font=f_sub, fill=MUTED)
        dev_y.append(y + ch // 2)

    # -- centre: the hub
    hx, hw, hy, hh = 560, 520, 90, 470
    d.rounded_rectangle((hx, hy, hx + hw, hy + hh), 24, fill=PANEL, outline=AMBER, width=3)
    photo = rounded(cover(P("docs", "photos", "hub-board.jpg"), hw - 40, 300), 14)
    im.paste(photo, (hx + 20, hy + 20))
    text_c(d, (hx + hw / 2, hy + 340), "THE HUB", f_big, AMBER)
    text_c(d, (hx + hw / 2, hy + 400), "RP2350 relay board · Wi-Fi + Bluetooth", f_sub, INK)
    text_c(d, (hx + hw / 2, hy + 428), "decides, switches, serves every screen", f_sub, MUTED)

    # Bluetooth links
    for k, y in enumerate(dev_y):
        a, b = (lx + lw + 6, y), (hx - 8, hy + 110 + k * 80)
        if k == 3:
            arrow(d, a, b, RED, both=True)            # the heater is controlled too
        else:
            arrow(d, a, b, BLUE)

    # -- right: the screens
    rx, rw = 1240, 420
    d.text((rx, 40), "WI-FI  ·  the same pages on all", font=f_head, fill=GREEN)
    screens = [(P("docs", "photos", "display-home.jpg"), "4\" cabin display", "fit"),
               (P("docs", "screenshots", "web-power.png"), "Phones, tablets, laptops", "fit"),
               (P("docs", "photos", "van-head-unit-power.jpg"), "Van head unit", "fit")]
    sh = 262
    for k, (path, label, mode) in enumerate(screens):
        y = 90 + k * (sh + 24)
        d.rounded_rectangle((rx, y, rx + rw, y + sh), 18, fill=PANEL)
        if mode == "phone":
            pic = fit(path, rw - 40, sh - 70)
            pic = rounded(pic, 16)
        else:
            pic = rounded(cover(path, rw - 40, sh - 70), 12)
        im.paste(pic, (rx + (rw - pic.width) // 2, y + 18))
        text_c(d, (rx + rw / 2, y + sh - 44), label, f_tag, INK)
        arrow(d, (hx + hw + 8, hy + 120 + k * 120), (rx - 8, y + sh // 2), GREEN, both=True)

    # -- below the hub: what is wired to it
    wy = 640
    arrow(d, (hx + 125, hy + hh + 6), (hx + 125, wy - 6), AMBER)
    arrow(d, (hx + hw - 125, wy - 6), (hx + hw - 125, hy + hh + 6), AMBER)
    for k, (title, chips, col) in enumerate(
            [("6 RELAYS", ["Water pump", "Fridge", "Lights", "Inverter", "Fuel pump", "USB"], AMBER),
             ("SENSORS", ["GPS", "Accelerometer", "Buzzer", "RGB light"], AMBER)]):
        w0 = (hw - 20) // 2
        x0 = hx + k * (w0 + 20)
        d.rounded_rectangle((x0, wy, x0 + w0, wy + 300), 18, fill=PANEL)
        d.text((x0 + 22, wy + 20), title, font=f_head, fill=col)
        cx, cy = x0 + 22, wy + 64
        for c in chips:
            w = d.textlength(c, font=f_sub) + 26
            if cx + w > x0 + w0 - 16:
                cx, cy = x0 + 22, cy + 46
            d.rounded_rectangle((cx, cy, cx + w, cy + 36), 18, fill=PANEL2)
            d.text((cx + 13, cy + 7), c, font=f_sub, fill=INK)
            cx += w + 10
    text_c(d, (hx + hw / 2, wy + 318), "WIRED  ·  12 V switched by the hub, and its sensors", f_tag, AMBER)

    # -- top: the internet, optional
    icon(d, "cloud", hx + hw / 2, 40, MUTED)
    d.text((hx + hw / 2 + 44, 26), "internet, optional: forecast, clock", font=f_sub, fill=MUTED)
    arrow(d, (hx + hw / 2, 76), (hx + hw / 2, hy - 4), MUTED, width=3, dash=True)
    im.save(os.path.join(OUT, "overview.png"), optimize=True)


def every_screen():
    shots = [(P("docs", "screenshots", "display-heater.png"), "Cabin display"),
             (P("docs", "screenshots", "web-heater.png"), "Phone or browser"),
             (P("docs", "photos", "van-head-unit-heater.jpg"), "Van head unit")]
    W, H, ph = 1700, 470, 360
    im = Image.new("RGB", (W, H), NIGHT)
    d = ImageDraw.Draw(im)
    f = font(26, "SemiBold")
    cw = (W - 80 - 2 * 30) // 3
    for k, (path, label) in enumerate(shots):
        x = 40 + k * (cw + 30)
        pic = rounded(fit(path, cw, ph), 14)
        im.paste(pic, (x + (cw - pic.width) // 2, 30 + (ph - pic.height) // 2))
        text_c(d, (x + cw / 2, 30 + ph + 26), label, f, INK)
        if k < 2:
            d.text((x + cw + 2, 30 + ph // 2 - 20), "=", font=font(40, "Bold"), fill=AMBER)
    im.save(os.path.join(OUT, "every-screen.png"), optimize=True)


def features():
    pages = [("power", "Power flow"), ("heater", "Diesel heater"), ("level", "Levelling"),
             ("switches", "Six switches"), ("tanks", "Water tanks"), ("gforce", "G-force"),
             ("forecast", "Forecast"), ("security", "Lights and guard"), ("games", "Games")]
    cols, sw, sh = 3, 480, 320
    gap, lab = 30, 50
    W = 40 * 2 + cols * sw + (cols - 1) * gap
    rows = (len(pages) + cols - 1) // cols
    H = 40 * 2 + rows * (sh + lab) + (rows - 1) * gap
    im = Image.new("RGB", (W, H), NIGHT)
    d = ImageDraw.Draw(im)
    f = font(24, "SemiBold")
    for k, (name, label) in enumerate(pages):
        x = 40 + (k % cols) * (sw + gap)
        y = 40 + (k // cols) * (sh + lab + gap)
        pic = rounded(Image.open(P("docs", "screenshots", "display-%s.png" % name)).convert("RGB"), 12)
        im.paste(pic, (x, y))
        text_c(d, (x + sw / 2, y + sh + 12), label, f, INK)
    im.save(os.path.join(OUT, "features.png"), optimize=True)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    overview()
    every_screen()
    features()
    print("written: docs/images/overview.png, every-screen.png, features.png")
