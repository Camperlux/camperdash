# Anti-aliased shapes for the display: discs, rings and arcs, thick lines with
# round ends, and convex polygons - drawn straight into the RGB565 frame
# buffer, each edge pixel blended with what is already there.
#
# framebuf's own circles and lines are hard-edged, which is what made the
# dial, the clock and the icons look rough next to the smooth text.
#
# The pixel loops are "viper" functions: MicroPython compiles them to machine
# code, which makes a 200 x 200 px ring a few milliseconds rather than a
# second. On the PC (tools/preview_display.py) the same functions run as plain
# Python. Viper limits the style: integers only, no calls inside the loops, at
# most four arguments - so every shape takes its settings as one int array.
#
# Coordinates in the kernels are in 1/16 px, so edges land between pixels and
# a hand turning a degree actually moves. The buffer holds big-endian RGB565
# (the panel's order; see st7796.py), two bytes a pixel.

from array import array

# The decorator must be written literally as @micropython.viper: the compiler
# recognises that spelling and emits machine code. An alias (viper = ...)
# compiles as an ordinary function - measured at ~15 us a pixel, 50x slower.
try:
    import micropython
except ImportError:                        # the PC preview
    class micropython:
        @staticmethod
        def viper(f):
            return f
    ptr8 = ptr32 = None                    # annotation names viper provides


# ---- kernels ------------------------------------------------------------------

@micropython.viper
def _ring(buf: ptr8, p: ptr32):
    # p: stride, x0, y0, x1, y1, cx, cy, rin, rout, r, g, b, mode, ax, ay, bx, by, alpha
    stride = p[0]
    x0 = p[1]
    y0 = p[2]
    x1 = p[3]
    y1 = p[4]
    cx = p[5]
    cy = p[6]
    rin = p[7]
    rout = p[8]
    fr = p[9]
    fg = p[10]
    fb = p[11]
    mode = p[12]
    ax = p[13]
    ay = p[14]
    bx = p[15]
    by = p[16]
    alpha = p[17]
    ro2 = rout * rout
    ro_far = (rout + 8) * (rout + 8)
    ri2 = rin * rin
    ri_near = 0
    if rin > 8:
        ri_near = (rin - 8) * (rin - 8)
    # A disc drawn solid: pixels well inside it take the colour outright
    solid_in = 0
    if rin == 0 and mode == 0 and alpha >= 255 and rout > 16:
        solid_in = (rout - 8) * (rout - 8)
    y = y0
    while y < y1:
        py = y * 16 + 8 - cy
        row = y * stride
        # This row's span of the ring, from an integer square root, so the
        # pixels either side of it - most of a big circle's box - are never
        # visited at all.
        v = ro_far - py * py
        if v <= 0:
            y += 1
            continue
        s = v
        t = (s + 1) >> 1
        while t < s:
            s = t
            t = (s + v // s) >> 1
        xa = (cx - s) >> 4
        xb = ((cx + s) >> 4) + 1
        if xa < x0:
            xa = x0
        if xb > x1:
            xb = x1
        # the hole of a ring: skip its middle too
        ha = xb
        hb = xb
        if ri_near > 0:
            v = ri_near - py * py
            if v > 0:
                s = v
                t = (s + 1) >> 1
                while t < s:
                    s = t
                    t = (s + v // s) >> 1
                ha = ((cx - s) >> 4) + 1
                hb = (cx + s) >> 4
        x = xa
        while x < xb:
            if x >= ha and x < hb:
                x = hb
                continue
            px = x * 16 + 8 - cx
            d2 = px * px + py * py
            if d2 < solid_in:
                i = (row + x) * 2
                buf[i] = (fr << 3) | (fg >> 3)
                buf[i + 1] = ((fg & 7) << 5) | fb
            elif d2 < ro_far and d2 >= ri_near:
                inside = 1
                if mode == 1:                       # sweep up to 180 degrees
                    if ax * py - ay * px < 0 or px * by - py * bx < 0:
                        inside = 0
                elif mode == 2:                     # sweep over 180: outside the gap
                    if bx * py - by * px > 0 and px * ay - py * ax > 0:
                        inside = 0
                if inside:
                    cov = (ro2 - d2) // (2 * rout) + 8        # 1/16 px of coverage
                    if rin > 0:
                        ci = (d2 - ri2) // (2 * rin) + 8
                        if ci < cov:
                            cov = ci
                    if cov > 0:
                        if cov > 16:
                            cov = 16
                        a = (cov * alpha) >> 4                # 0..255
                        i = (row + x) * 2
                        c = (buf[i] << 8) | buf[i + 1]
                        r = c >> 11
                        g = (c >> 5) & 63
                        b = c & 31
                        r = r + (((fr - r) * a) >> 8)
                        g = g + (((fg - g) * a) >> 8)
                        b = b + (((fb - b) * a) >> 8)
                        c = (r << 11) | (g << 5) | b
                        buf[i] = c >> 8
                        buf[i + 1] = c & 255
            x += 1
        y += 1


@micropython.viper
def _capsule(buf: ptr8, p: ptr32):
    # p: stride, x0, y0, x1, y1, ax, ay, dx, dy, len2, hw, r, g, b, alpha
    stride = p[0]
    x0 = p[1]
    y0 = p[2]
    x1 = p[3]
    y1 = p[4]
    sx = p[5]
    sy = p[6]
    dx = p[7]
    dy = p[8]
    len2 = p[9]
    hw = p[10]
    fr = p[11]
    fg = p[12]
    fb = p[13]
    alpha = p[14]
    hw2 = hw * hw
    far = (hw + 8) * (hw + 8)
    y = y0
    while y < y1:
        qy = y * 16 + 8 - sy
        row = y * stride
        x = x0
        while x < x1:
            qx = x * 16 + 8 - sx
            # nearest point on the segment, t in 1/1024
            # (the divisor is scaled down rather than the dot product up, so a
            # line across the whole screen cannot overflow 32 bits)
            t = 0
            den = len2 >> 10
            if den > 0:
                t = (qx * dx + qy * dy) // den
                if t < 0:
                    t = 0
                if t > 1024:
                    t = 1024
            ex = qx - ((dx * t) >> 10)
            ey = qy - ((dy * t) >> 10)
            d2 = ex * ex + ey * ey
            if d2 < far:
                cov = (hw2 - d2) // (2 * hw) + 8
                if cov > 0:
                    if cov > 16:
                        cov = 16
                    a = (cov * alpha) >> 4
                    i = (row + x) * 2
                    c = (buf[i] << 8) | buf[i + 1]
                    r = c >> 11
                    g = (c >> 5) & 63
                    b = c & 31
                    r = r + (((fr - r) * a) >> 8)
                    g = g + (((fg - g) * a) >> 8)
                    b = b + (((fb - b) * a) >> 8)
                    c = (r << 11) | (g << 5) | b
                    buf[i] = c >> 8
                    buf[i + 1] = c & 255
            x += 1
        y += 1


@micropython.viper
def _poly(buf: ptr8, p: ptr32):
    # p: stride, x0, y0, x1, y1, n, r, g, b, alpha, then n x (nx, ny, c):
    # an edge's inward distance, in 1/16 px, is (nx*X + ny*Y) >> 10 + c
    stride = p[0]
    x0 = p[1]
    y0 = p[2]
    x1 = p[3]
    y1 = p[4]
    n = p[5]
    fr = p[6]
    fg = p[7]
    fb = p[8]
    alpha = p[9]
    y = y0
    while y < y1:
        Y = y * 16 + 8
        row = y * stride
        x = x0
        while x < x1:
            X = x * 16 + 8
            m = 100000
            k = 0
            while k < n:
                d = ((p[10 + 3 * k] * X + p[11 + 3 * k] * Y) >> 10) + p[12 + 3 * k]
                if d < m:
                    m = d
                k += 1
            cov = m + 8
            if cov > 0:
                if cov > 16:
                    cov = 16
                a = (cov * alpha) >> 4
                i = (row + x) * 2
                c = (buf[i] << 8) | buf[i + 1]
                r = c >> 11
                g = (c >> 5) & 63
                b = c & 31
                r = r + (((fr - r) * a) >> 8)
                g = g + (((fg - g) * a) >> 8)
                b = b + (((fb - b) * a) >> 8)
                c = (r << 11) | (g << 5) | b
                buf[i] = c >> 8
                buf[i + 1] = c & 255
            x += 1
        y += 1


@micropython.viper
def _rrect(buf: ptr8, p: ptr32):
    # p: stride, x0, y0, x1, y1, cx, cy, hw, hh, rad, r, g, b, alpha
    # A rounded rectangle as one distance field, so its straight sides and
    # corners meet without seams.
    stride = p[0]
    x0 = p[1]
    y0 = p[2]
    x1 = p[3]
    y1 = p[4]
    cx = p[5]
    cy = p[6]
    ix = p[7] - p[9]                 # half-size of the square-cornered core
    iy = p[8] - p[9]
    rad = p[9]
    fr = p[10]
    fg = p[11]
    fb = p[12]
    alpha = p[13]
    r2 = rad * rad
    y = y0
    while y < y1:
        dy = y * 16 + 8 - cy
        if dy < 0:
            dy = -dy
        dy = dy - iy
        if dy < 0:
            dy = 0
        row = y * stride
        x = x0
        while x < x1:
            dx = x * 16 + 8 - cx
            if dx < 0:
                dx = -dx
            dx = dx - ix
            if dx < 0:
                dx = 0
            d2 = dx * dx + dy * dy
            cov = (r2 - d2) // (2 * rad) + 8
            if cov > 0:
                if cov > 16:
                    cov = 16
                a = (cov * alpha) >> 4
                i = (row + x) * 2
                if a >= 255:
                    c = (fr << 11) | (fg << 5) | fb
                else:
                    c = (buf[i] << 8) | buf[i + 1]
                    r = c >> 11
                    g = (c >> 5) & 63
                    b = c & 31
                    r = r + (((fr - r) * a) >> 8)
                    g = g + (((fg - g) * a) >> 8)
                    b = b + (((fb - b) * a) >> 8)
                    c = (r << 11) | (g << 5) | b
                buf[i] = c >> 8
                buf[i + 1] = c & 255
            x += 1
        y += 1


# ---- the drawing API ------------------------------------------------------------

class AA:
    """Smooth shapes on one frame buffer. Colours are the panel colours from
    gfx.rgb() (byte-swapped RGB565)."""

    def __init__(self, buf, w, h, fb=None):
        self.buf, self.w, self.h = buf, w, h
        self.fb = fb                 # framebuf over buf, for fast solid fills
        self._p = array("i", [0] * 64)

    @staticmethod
    def _rgb(col):
        c = ((col & 255) << 8) | (col >> 8)               # undo the panel swap
        return c >> 11, (c >> 5) & 63, c & 31

    def _box(self, x0, y0, x1, y1):
        return (max(0, int(x0)), max(0, int(y0)), min(self.w, int(x1) + 1),
                min(self.h, int(y1) + 1))

    def ring(self, cx, cy, rin, rout, col, a0=None, a1=None, alpha=255):
        """A ring between radii rin and rout (rin 0: a disc), optionally only
        from angle a0 to a1 - radians, clockwise from 12 o'clock."""
        import math
        p = self._p
        if a0 is not None and a1 - a0 < 6.28:
            # Only the pixels round the arc itself: a slice of the dial's
            # gradient is a few hundred pixels, not the whole 200 x 200 box.
            xs, ys = [], []
            n = max(2, int((a1 - a0) / 0.35) + 2)
            for i in range(n + 1):
                t = a0 + (a1 - a0) * i / n
                st, ct = math.sin(t), math.cos(t)
                for rr in (rin, rout):
                    xs.append(cx + st * rr)
                    ys.append(cy - ct * rr)
            x0, y0, x1, y1 = self._box(min(xs) - 2, min(ys) - 2, max(xs) + 2, max(ys) + 2)
        else:
            x0, y0, x1, y1 = self._box(cx - rout - 1, cy - rout - 1, cx + rout + 1, cy + rout + 1)
        r, g, b = self._rgb(col)
        mode, ax, ay, bx, by = 0, 0, 0, 0, 0
        if a0 is not None and a1 - a0 < 6.28:
            mode = 1 if a1 - a0 <= math.pi else 2
            ax, ay = int(math.sin(a0) * 1024), int(-math.cos(a0) * 1024)
            bx, by = int(math.sin(a1) * 1024), int(-math.cos(a1) * 1024)
        vals = (self.w, x0, y0, x1, y1, int(cx * 16), int(cy * 16), int(rin * 16),
                int(rout * 16), r, g, b, mode, ax, ay, bx, by, alpha)
        i = 0
        for v in vals:
            p[i] = v
            i += 1
        _ring(self.buf, p)

    def disc(self, cx, cy, r, col, alpha=255):
        self.ring(cx, cy, 0, r, col, alpha=alpha)

    def arc(self, cx, cy, rin, rout, a0, a1, col, alpha=255, caps=True):
        """A thick arc with round ends."""
        import math
        self.ring(cx, cy, rin, rout, col, a0, a1, alpha)
        if caps:
            rm, rr = (rin + rout) / 2, (rout - rin) / 2
            for a in (a0, a1):
                self.disc(cx + math.sin(a) * rm, cy - math.cos(a) * rm, rr, col, alpha)

    def line(self, x0, y0, x1, y1, width, col, alpha=255):
        """A thick line with round ends."""
        hw = width / 2
        p = self._p
        bx0, by0, bx1, by1 = self._box(min(x0, x1) - hw - 1, min(y0, y1) - hw - 1,
                                       max(x0, x1) + hw + 1, max(y0, y1) + hw + 1)
        r, g, b = self._rgb(col)
        dx, dy = int((x1 - x0) * 16), int((y1 - y0) * 16)
        vals = (self.w, bx0, by0, bx1, by1, int(x0 * 16), int(y0 * 16), dx, dy,
                dx * dx + dy * dy, max(1, int(hw * 16)), r, g, b, alpha)
        i = 0
        for v in vals:
            p[i] = v
            i += 1
        _capsule(self.buf, p)

    def poly(self, pts, col, alpha=255):
        """A filled convex polygon, points clockwise on screen (y down)."""
        import math
        n = len(pts)
        if n < 3 or n > 16:
            return
        p = self._p
        xs = [q[0] for q in pts]
        ys = [q[1] for q in pts]
        x0, y0, x1, y1 = self._box(min(xs) - 1, min(ys) - 1, max(xs) + 1, max(ys) + 1)
        r, g, b = self._rgb(col)
        for i, v in enumerate((self.w, x0, y0, x1, y1, n, r, g, b, alpha)):
            p[i] = v
        for k in range(n):
            ax, ay = pts[k]
            bx, by = pts[(k + 1) % n]
            ex, ey = bx - ax, by - ay
            ln = math.sqrt(ex * ex + ey * ey) or 1
            # inward normal for a clockwise polygon, y down: (-ey, ex)
            nx, ny = -ey / ln, ex / ln
            p[10 + 3 * k] = int(nx * 1024)
            p[11 + 3 * k] = int(ny * 1024)
            p[12 + 3 * k] = int(-(nx * ax + ny * ay) * 16)
        _poly(self.buf, p)

    def rrect(self, x, y, w, h, r, col, alpha=255, clip_top=None):
        """A filled rectangle with smooth rounded corners. clip_top: draw only
        from that row down - a card's shadow shows only below the card, so
        there is no point blending it under the whole of it."""
        p = self._p
        x0, y0, x1, y1 = self._box(x - 1, y - 1, x + w + 1, y + h + 1)
        if clip_top is not None and clip_top > y0:
            y0 = int(clip_top)
        rr, g, b = self._rgb(col)
        r = max(1, min(r, w / 2, h / 2))
        vals = [self.w, x0, y0, x1, y1, int((x + w / 2) * 16), int((y + h / 2) * 16),
                int(w * 8), int(h * 8), int(r * 16), rr, g, b, alpha]
        i = 0
        for v in vals:
            p[i] = v
            i += 1
        whole = (x == int(x) and y == int(y) and w == int(w) and h == int(h)
                 and r == int(r))
        if self.fb is None or alpha < 255 or not whole:
            _rrect(self.buf, p)
            return
        # Opaque and on whole pixels: the straight sides need no smoothing, so
        # framebuf fills everything but the corners in C, and only the four
        # r x r corners go through the per-pixel kernel (~1 ms, not ~28 ms).
        x, y, w, h, r = int(x), int(y), int(w), int(h), int(r)
        self.fb.fill_rect(x + r, y, w - 2 * r, h, col)
        self.fb.fill_rect(x, y + r, r, h - 2 * r, col)
        self.fb.fill_rect(x + w - r, y + r, r, h - 2 * r, col)
        for bx, by in ((x, y), (x + w - r, y), (x, y + h - r), (x + w - r, y + h - r)):
            bx0, by0, bx1, by1 = self._box(bx, by, bx + r - 1, by + r - 1)
            p[1], p[2], p[3], p[4] = bx0, by0, bx1, by1
            _rrect(self.buf, p)
