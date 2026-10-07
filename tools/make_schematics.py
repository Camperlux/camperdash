"""Draw the hub's wiring schematic as SVG, for docs/HARDWARE.md.

  python tools/make_schematics.py          # writes docs/schematics/hub_wiring.svg

Drawn from the pin assignments the firmware actually uses (pico/config.py on
the live hub, the board files in firmware/boards/), so when wiring changes,
change it here and redraw. Plain SVG with no dependencies: GitHub shows it
in the page.
"""

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "schematics")

W, H = 1400, 960
INK, MUTED, RED, BLUE, GREEN, AMBER, PAPER = "#1d1f24", "#6b6f7a", "#c0392b", "#2c6fbb", "#2e8b57", "#b7791f", "#ffffff"
FONT = "font-family='DejaVu Sans, Arial, sans-serif'"

out = []


def text(x, y, s, size=13, col=INK, anchor="start", weight="normal"):
    out.append("<text x='%d' y='%d' %s font-size='%d' fill='%s' text-anchor='%s' font-weight='%s'>%s</text>"
               % (x, y, FONT, size, col, anchor, weight, s.replace("&", "&amp;").replace("<", "&lt;")))


def box(x, y, w, h, title, sub=None, col=INK, fill="#f6f7f9", r=8):
    out.append("<rect x='%d' y='%d' width='%d' height='%d' rx='%d' fill='%s' stroke='%s' stroke-width='2'/>"
               % (x, y, w, h, r, fill, col))
    text(x + w // 2, y + 22, title, 15, col, "middle", "bold")
    if sub:
        text(x + w // 2, y + 40, sub, 11, MUTED, "middle")


def pin(x, y, label, side="left"):
    """A terminal on a box edge: a small square and its label inside the box."""
    out.append("<rect x='%d' y='%d' width='10' height='10' fill='%s' stroke='%s'/>" % (x - 5, y - 5, PAPER, INK))
    if side == "left":
        text(x + 10, y + 4, label, 11)
    else:
        text(x - 10, y + 4, label, 11, anchor="end")


def wire(points, col=INK, width=2, dash=None):
    d = " ".join("%s%d,%d" % ("M" if i == 0 else "L", x, y) for i, (x, y) in enumerate(points))
    out.append("<path d='%s' fill='none' stroke='%s' stroke-width='%d'%s/>"
               % (d, col, width, " stroke-dasharray='%s'" % dash if dash else ""))


def dot(x, y, col=INK):
    out.append("<circle cx='%d' cy='%d' r='4' fill='%s'/>" % (x, y, col))


def fuse(x, y, label, vertical=False):
    if vertical:
        out.append("<rect x='%d' y='%d' width='14' height='30' fill='%s' stroke='%s' stroke-width='2'/>" % (x - 7, y, PAPER, RED))
        text(x + 12, y + 20, label, 11, RED)
    else:
        out.append("<rect x='%d' y='%d' width='30' height='14' fill='%s' stroke='%s' stroke-width='2'/>" % (x, y - 7, PAPER, RED))
        text(x + 15, y - 12, label, 11, RED, "middle")


def hub_wiring():
    out.clear()
    out.append("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 %d %d' width='%d' height='%d'>" % (W, H, W, H))
    out.append("<rect width='100%%' height='100%%' fill='%s'/>" % PAPER)
    text(30, 40, "CamperDash hub - wiring", 22, INK, weight="bold")
    text(30, 62, "Waveshare RP2350-Relay-6CH-W. Pins as used by the firmware; check the board's own silkscreen and datasheet before wiring.", 12, MUTED)

    # ---- the hub board ----
    hx, hy, hw, hh = 470, 110, 420, 720
    box(hx, hy, hw, hh, "HUB  Waveshare RP2350-Relay-6CH-W", "RP2350B, 16 MB flash, CYW43439 Wi-Fi + Bluetooth LE")
    text(hx + hw // 2, hy + 66, "On board: buzzer GPIO23, RGB LED (WS2812) GPIO36, USB-C", 11, MUTED, "middle")

    # left side: power, RS485, expansion
    L = hx
    left_pins = [
        (170, "DC + (7-36 V)"), (200, "DC -"),
        (290, "RS485 A+"), (320, "RS485 B-"), (350, "RS485 GND"),
        (440, "3V3"), (470, "GND"), (500, "GPIO4  I2C SDA"), (530, "GPIO5  I2C SCL"),
        (590, "GPIO0  UART0 TX"), (620, "GPIO1  UART0 RX"), (680, "GPIO6  1-Wire (planned)"),
    ]
    for y, lab in left_pins:
        pin(L, hy + y - 110 + 110, lab, "left")

    # right side: relays
    R = hx + hw
    for i in range(6):
        y = hy + 140 + i * 90
        pin(R, y, "CH%d COM" % (i + 1), "right")
        pin(R, y + 26, "CH%d NO" % (i + 1), "right")
        text(R - 150, y + 14, "relay %d = GPIO%d" % (i + 1, 26 + i), 10, MUTED, "end")

    # antenna
    out.append("<path d='M%d,%d l0,-30 m-12,0 l12,14 l12,-14' fill='none' stroke='%s' stroke-width='2'/>" % (hx + 60, hy, INK))
    text(hx + 80, hy - 18, "Wi-Fi / BLE antenna (SMA)", 11, MUTED)

    # ---- power in ----
    bx, by = 60, 150
    box(bx, by, 190, 110, "LEISURE BATTERY", "12 V lithium (Fogstar), BMS", RED, "#fdf2f1")
    y_pos, y_neg = hy + 170 - 110 + 110, hy + 200 - 110 + 110
    wire([(bx + 190, by + 40), (330, by + 40), (330, y_pos), (L - 5, y_pos)], RED, 3)
    fuse(360, y_pos, "2 A")
    wire([(bx + 190, by + 80), (300, by + 80), (300, y_neg), (L - 5, y_neg)], INK, 3)
    text(60, by + 128, "Hub draws well under 0.5 A;", 11, MUTED)
    text(60, by + 143, "fuse close to the battery.", 11, MUTED)

    # ---- relay loads (high-side switching) ----
    fx = 1000
    wire([(fx, 160), (fx, 740)], RED, 3)
    text(fx + 8, 760, "+12 V fused distribution", 11, RED)
    loads = ["Water pump", "Fridge", "Garage light", "Inverter (remote line)", "Master lights", "Fuel pump"]
    for i, name in enumerate(loads):
        y = hy + 140 + i * 90
        wire([(R + 5, y), (fx, y)], RED, 2)
        dot(fx, y, RED)
        fuse(fx - 70, y, "per load")
        wire([(R + 5, y + 26), (1120, y + 26)], AMBER, 2)
        out.append("<rect x='1120' y='%d' width='150' height='34' rx='6' fill='#fff8ec' stroke='%s' stroke-width='2'/>" % (y + 9, AMBER))
        text(1195, y + 31, name, 12, INK, "middle")
        wire([(1270, y + 26), (1320, y + 26), (1320, y + 46)], INK, 2)
        out.append("<path d='M1308,%d l24,0 m-18,5 l12,0 m-7,5 l2,0' stroke='%s' stroke-width='2'/>" % (y + 46, INK))
    text(1120, 100, "Loads: the relay closes COM to NO.", 11, MUTED)
    text(1120, 116, "Names and icons are set in Settings.", 11, MUTED)

    # ---- sensors on the expansion pins ----
    sx = 90
    y_3v3, y_gnd = hy + 440 - 110 + 110, hy + 470 - 110 + 110
    y_sda, y_scl = hy + 500 - 110 + 110, hy + 530 - 110 + 110
    y_tx, y_rx = hy + 590 - 110 + 110, hy + 620 - 110 + 110
    y_ow = hy + 680 - 110 + 110

    box(sx, 470, 220, 90, "ACCELEROMETER", "MPU-6050 (0x68) or LIS3DH (0x19)", BLUE, "#eef4fb")
    wire([(sx + 220, 500), (400, 500), (400, y_sda), (L - 5, y_sda)], BLUE)
    wire([(sx + 220, 525), (390, 525), (390, y_scl), (L - 5, y_scl)], BLUE)
    text(sx + 225, 494, "SDA", 10, BLUE)
    text(sx + 225, 519, "SCL", 10, BLUE)

    box(sx, 600, 220, 80, "GPS", "u-blox NEO-7M, NMEA 9600", GREEN, "#eff8f2")
    wire([(sx + 220, 625), (380, 625), (380, y_rx), (L - 5, y_rx)], GREEN)
    text(sx + 225, 619, "TX -> hub RX", 10, GREEN)
    wire([(sx + 220, 650), (370, 650), (370, y_tx), (L - 5, y_tx)], GREEN, dash="5,4")
    text(sx + 225, 664, "RX (optional)", 10, GREEN)

    box(sx, 720, 220, 70, "TEMP PROBE", "DS18B20 (planned)", MUTED, "#f4f4f6")
    wire([(sx + 220, 750), (L - 5, 750), (L - 5, y_ow)], MUTED, dash="5,4")
    text(sx + 225, 744, "DQ, 4.7k to 3V3", 10, MUTED)

    # shared 3V3 / GND rails to sensors
    wire([(L - 5, y_3v3), (430, y_3v3), (430, 860), (60, 860), (60, 480), (sx, 480)], RED, 2)
    wire([(60, 610), (sx, 610)], RED, 2)
    wire([(60, 730), (sx, 730)], RED, 2)
    dot(60, 610, RED)
    dot(60, 730, RED)
    wire([(L - 5, y_gnd), (440, y_gnd), (440, 875), (45, 875), (45, 545), (sx, 545)], INK, 2)
    wire([(45, 668), (sx, 668)], INK, 2)
    wire([(45, 778), (sx, 778)], INK, 2)
    dot(45, 668)
    dot(45, 778)
    text(40, 900, "3V3 and GND from the hub's header feed", 11, MUTED)
    text(40, 915, "every sensor (red = 3V3, black = GND).", 11, MUTED)

    # ---- RS485 ----
    box(60, 320, 190, 90, "RS485 (spare)", "Modbus RTU, auto-direction", MUTED, "#f4f4f6")
    for k, y in enumerate((hy + 290 - 110 + 110, hy + 320 - 110 + 110, hy + 350 - 110 + 110)):
        wire([(250, 345 + k * 22), (290 + k * 10, 345 + k * 22), (290 + k * 10, y), (L - 5, y)], MUTED, dash="5,4")
    text(60, 430, "UART1 GPIO24/25; driver pico/rs485.py", 11, MUTED)

    # ---- wireless links ----
    text(470, 880, "Wireless, no wiring: Fogstar BMS, Renogy DC-DC (BT-2), JP diesel heater over Bluetooth LE;", 12, INK)
    text(470, 898, "Victron Blue Smart IP22 charger by its encrypted Bluetooth adverts; the 4\" display, phones and the", 12, INK)
    text(470, 916, "van's head unit over Wi-Fi (the hub's hotspot \"Camperlux\", 192.168.4.1, or a shared network).", 12, INK)
    out.append("</svg>")
    return "\n".join(out)


def main():
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, "hub_wiring.svg")
    with open(p, "w", encoding="utf-8") as f:
        f.write(hub_wiring())
    print("wrote", p)


if __name__ == "__main__":
    main()
