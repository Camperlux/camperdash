# Line icons for the display: 2 px strokes in an 18 px box, drawn smooth with
# aa.py, so they scale to any size and take any colour. Drawn in code rather
# than stored as images: a few hundred bytes for the whole set.

import math


def draw(a, name, x, y, col, s=18):
    """Icon name with its top-left at (x, y), s pixels square."""
    k = s / 18.0

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
        # an outline flame: a round base, a tip leaning right, and a second
        # tongue on the left - the notch is what stops it reading as a drop
        a.arc(x + 9 * k, y + 12 * k, 4.6 * k, 6.6 * k, 1.5, 4.8, col)
        L(14.6, 12, 14, 7.5); L(14, 7.5, 10.5, 1.2); L(10.5, 1.2, 9.6, 6)
        L(9.6, 6, 7.2, 3.6); L(7.2, 3.6, 3.8, 9); L(3.8, 9, 3.4, 12)
        a.disc(x + 9 * k, y + 13.4 * k, 2.3 * k, col)
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
            a.arc(x + 9 * k, y + 15 * k, (r - 1.1) * k, (r + 1.1) * k, -0.8, 0.8, col)
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
    elif name == "snow":
        for i in range(3):
            t = i * math.pi / 3
            L(9 + math.sin(t) * 8, 9 - math.cos(t) * 8, 9 - math.sin(t) * 8, 9 + math.cos(t) * 8)
    elif name == "tick":
        L(3, 9.5, 7.5, 14); L(7.5, 14, 15.5, 4.5)
    elif name == "back":
        L(11, 3, 5, 9, 2.4); L(5, 9, 11, 15, 2.4)
    elif name == "fan":
        # a three-bladed fan in its ring
        a.ring(x + 9 * k, y + 9 * k, 7.1 * k, 8.8 * k, col)
        for i in range(3):
            t = i * 2 * math.pi / 3
            L(9, 9, 9 + math.sin(t) * 5.3, 9 - math.cos(t) * 5.3, 3.2)
    elif name == "tv":
        L(1, 2.5, 17, 2.5); L(17, 2.5, 17, 13.5); L(17, 13.5, 1, 13.5); L(1, 13.5, 1, 2.5)
        L(9, 13.5, 9, 16.5); L(5.5, 16.5, 12.5, 16.5)
    elif name == "music":
        L(6.5, 14, 6.5, 4); L(6.5, 4, 15, 2, 2.4); L(15, 2, 15, 12)
        a.disc(x + 4.6 * k, y + 14.2 * k, 2.3 * k, col)
        a.disc(x + 13.1 * k, y + 12.2 * k, 2.3 * k, col)
    elif name == "usb":
        # the USB trident
        L(9, 3, 9, 14.5); L(9, 11, 5, 8.5); L(5, 8.5, 5, 6.6); L(9, 9, 13, 6.5); L(13, 6.5, 13, 4.9)
        a.poly(((x + 9 * k, y + 0.5 * k), (x + 11.4 * k, y + 4 * k), (x + 6.6 * k, y + 4 * k)), col)
        a.disc(x + 5 * k, y + 5.4 * k, 1.6 * k, col)
        a.rrect(x + 11.7 * k, y + 2.4 * k, 2.6 * k, 2.6 * k, 0.5, col)
        a.disc(x + 9 * k, y + 15.4 * k, 2.1 * k, col)
    elif name == "cup":
        # a mug with steam: the kettle, the coffee machine
        L(3, 6, 13, 6); L(13, 6, 12, 15.5); L(12, 15.5, 4, 15.5); L(4, 15.5, 3, 6)
        a.arc(x + 13.4 * k, y + 10.4 * k, 1.5 * k, 3.2 * k, 0.2, 2.94, col)
        L(6, 1.5, 6, 3.8, 1.6); L(9.5, 1.5, 9.5, 3.8, 1.6)
    elif name == "bulb":
        a.ring(x + 9 * k, y + 7.5 * k, 4.9 * k, 6.7 * k, col)
        L(6.5, 12.5, 7, 14.5); L(11.5, 12.5, 11, 14.5); L(7, 15, 11, 15); L(7.5, 17, 10.5, 17)
    elif name == "siren":
        a.arc(x + 9 * k, y + 11 * k, 4.2 * k, 6.2 * k, -1.57, 1.57, col, caps=False)
        L(2.5, 16, 15.5, 16, 2.4); L(3, 11, 3, 16); L(15, 11, 15, 16)
        L(9, 1, 9, 3); L(2.5, 3.5, 4, 5); L(15.5, 3.5, 14, 5)
    elif name == "plug":
        L(6.5, 1, 6.5, 5); L(11.5, 1, 11.5, 5)
        L(3.5, 5, 14.5, 5); L(3.5, 5, 3.5, 9); L(14.5, 5, 14.5, 9)
        a.arc(x + 9 * k, y + 9 * k, 4.5 * k, 6.5 * k, 1.57, 4.71, col, caps=False)
        L(9, 14.5, 9, 17.5)
    elif name == "engine":
        # an alternator: its pulley, and the finned body behind it
        a.ring(x + 5 * k, y + 11 * k, 2.6 * k, 4.4 * k, col)
        L(8.5, 4, 17, 4); L(17, 4, 17, 17); L(17, 17, 8.5, 17); L(8.5, 17, 8.5, 4)
        L(11.3, 7, 11.3, 14, 1.5); L(14.2, 7, 14.2, 14, 1.5)
    elif name == "switch":
        # a toggle switch: a capsule with its knob to the right (on)
        L(5.5, 4.5, 12.5, 4.5, 1.8); L(5.5, 13.5, 12.5, 13.5, 1.8)
        a.arc(x + 5.5 * k, y + 9 * k, 3.6 * k, 5.4 * k, math.pi, 2 * math.pi, col, caps=False)
        a.arc(x + 12.5 * k, y + 9 * k, 3.6 * k, 5.4 * k, 0, math.pi, col, caps=False)
        a.disc(x + 12.5 * k, y + 9 * k, 2.6 * k, col)
    elif name == "alarm":
        a.ring(x + 9 * k, y + 10 * k, 6.3 * k, 8.1 * k, col)
        L(9, 10, 9, 6.5, 1.8); L(9, 10, 11.5, 11.5, 1.8)
        L(1.5, 4, 4.5, 1.5); L(16.5, 4, 13.5, 1.5)
