# Pack the Van: falling blocks, packed into the back of the van.
#
# Luggage comes in seven shapes. Slide it with the arrows, turn it, drop it;
# fill a whole row across the van and it clears. Every ten rows the luggage
# comes faster. The van is full when the next piece has nowhere to go.

import asyncio
import random
import time

import gfx
from arcade import (Base, W, H, HUD_H, C, BG, PANEL, PANEL2, LINE, TXT, MUTED, GOLD,
                    RED, GREEN, BLUE, AMBER)

COLS, ROWS, CELL = 10, 16, 17
BX, BY = 155, HUD_H + 8                 # the load space, 170 x 272
SHAPES = (  # cells as (col, row) in a 4 x 4 box, and the luggage's colour
    (((0, 1), (1, 1), (2, 1), (3, 1)), C(88, 166, 255)),     # surfboard
    (((0, 0), (1, 0), (0, 1), (1, 1)), C(211, 169, 74)),     # cool box
    (((1, 0), (0, 1), (1, 1), (2, 1)), C(180, 110, 220)),    # rucksack
    (((1, 0), (2, 0), (0, 1), (1, 1)), C(63, 185, 80)),      # tent bag
    (((0, 0), (1, 0), (1, 1), (2, 1)), C(248, 81, 73)),      # suitcase
    (((0, 0), (0, 1), (1, 1), (2, 1)), C(57, 211, 195)),     # chairs
    (((2, 0), (0, 1), (1, 1), (2, 1)), C(255, 157, 60)))     # bike
SCORE = (0, 100, 300, 500, 800)
# all four on the right, under the thumb
BTN = {"left": (334, 170, 66, 70), "right": (406, 170, 66, 70),
       "turn": (334, 248, 66, 64), "drop": (406, 248, 66, 64)}


_shade = {}


def _light(c, k):
    """A block's lit or shaded edge: worked out once for each colour."""
    v = _shade.get((c, k))
    if v is None:
        v = gfx.blend(c, C(255, 255, 255), k) if k > 0 else gfx.blend(c, C(0, 0, 0), -k)
        _shade[(c, k)] = v
    return v


class Game(Base):
    KEY = "pack"
    TITLE = "Pack the Van"

    def new(self):
        self.grid = [[None] * COLS for _ in range(ROWS)]
        self.score, self.lines, self.level = 0, 0, 1
        self.next = random.getrandbits(8) % 7
        self.over = False
        self.spawn()

    def spawn(self):
        self.kind, self.next = self.next, random.getrandbits(8) % 7
        self.cells = list(SHAPES[self.kind][0])
        self.px, self.py = 3, 0
        if not self.fits(self.cells, self.px, self.py):
            self.over = True
            self.sfx("bad")
            if self.record(self.score):
                self.sfx("chime")

    def fits(self, cells, px, py):
        g = self.grid
        for cx, cy in cells:
            x, y = px + cx, py + cy
            if x < 0 or x >= COLS or y >= ROWS or (y >= 0 and g[y][x] is not None):
                return False
        return True

    def move(self, dx):
        if self.fits(self.cells, self.px + dx, self.py):
            self.px += dx
            return True
        return False

    def turn(self):
        if self.kind == 1:
            return                                   # the cool box is square
        n = 4 if self.kind == 0 else 3
        cells = [(n - 1 - cy, cx) for cx, cy in self.cells]
        for kick in (0, -1, 1, -2, 2):                # nudge off a wall to turn
            if self.fits(cells, self.px + kick, self.py):
                self.cells, self.px = cells, self.px + kick
                self.sfx("tap")
                return

    def land(self):
        col = SHAPES[self.kind][1]
        for cx, cy in self.cells:
            if self.py + cy >= 0:
                self.grid[self.py + cy][self.px + cx] = col
        full = [y for y in range(ROWS) if all(c is not None for c in self.grid[y])]
        if full:
            for y in full:
                del self.grid[y]
                self.grid.insert(0, [None] * COLS)
            self.lines += len(full)
            self.score += SCORE[len(full)] * self.level
            self.level = 1 + self.lines // 10
            self.sfx("ok" if len(full) < 4 else "chime")
        else:
            self.sfx("hop")
        self.spawn()

    def fall(self):
        if self.fits(self.cells, self.px, self.py + 1):
            self.py += 1
        else:
            self.land()

    def drop(self):
        while self.fits(self.cells, self.px, self.py + 1):
            self.py += 1
            self.score += 2
        self.land()

    # -- drawing ---------------------------------------------------------------

    def block(self, x, y, col, s=CELL):
        fb = self.fb
        fb.fill_rect(x, y, s - 1, s - 1, col)
        fb.hline(x, y, s - 1, _light(col, 0.35))
        fb.vline(x, y, s - 1, _light(col, 0.35))
        fb.hline(x, y + s - 2, s - 1, _light(col, -0.35))
        fb.vline(x + s - 2, y, s - 1, _light(col, -0.35))

    def button(self, name, label):
        x, y, w, h = BTN[name]
        gfx.rrect(self.fb, x, y, w, h, 12, PANEL2)
        ft = self.f.lg if len(label) <= 2 else self.f.md      # words in the narrower buttons
        ft.text(self.fb, label, x + w // 2, y + (h - ft.height) // 2, TXT, PANEL2, 1)

    def draw(self):
        fb, f = self.fb, self.f
        fb.fill(BG)
        self.hud("Score %d" % self.score, "Best %d" % self.best)
        # the van's load space, its roof curving over the top
        gfx.rrect(fb, BX - 6, BY - 6, COLS * CELL + 12, ROWS * CELL + 10, 12, C(142, 138, 154))
        fb.fill_rect(BX, BY, COLS * CELL, ROWS * CELL, C(20, 19, 24))
        for x in range(1, COLS):
            fb.vline(BX + x * CELL - 1, BY, ROWS * CELL, C(27, 26, 32))
        for y, row in enumerate(self.grid):
            for x, col in enumerate(row):
                if col is not None:
                    self.block(BX + x * CELL, BY + y * CELL, col)
        if not self.over:
            # where it would land, faintly; then the piece itself
            gy = self.py
            while self.fits(self.cells, self.px, gy + 1):
                gy += 1
            for cx, cy in self.cells:
                if gy + cy >= 0:
                    fb.rect(BX + (self.px + cx) * CELL, BY + (gy + cy) * CELL, CELL - 1, CELL - 1, LINE)
            col = SHAPES[self.kind][1]
            for cx, cy in self.cells:
                if self.py + cy >= 0:
                    self.block(BX + (self.px + cx) * CELL, BY + (self.py + cy) * CELL, col)
        # next piece, and the figures
        gfx.rrect(fb, 8, BY, 138, 96, 12, PANEL)
        f.sm.text(fb, "NEXT", 18, BY + 8, MUTED, PANEL)
        shp, col = SHAPES[self.next]
        for cx, cy in shp:
            self.block(40 + cx * 15, BY + 40 + cy * 15, col, 15)
        f.sm.text(fb, "Rows %d" % self.lines, 12, BY + 112, TXT, BG)
        f.sm.text(fb, "Level %d" % self.level, 12, BY + 136, GOLD, BG)
        gfx.rrect(fb, 334, BY, 138, 120, 12, PANEL)
        f.sm.text(fb, "Fill a row", 403, BY + 14, MUTED, PANEL, 1)
        f.sm.text(fb, "across the", 403, BY + 36, MUTED, PANEL, 1)
        f.sm.text(fb, "van to clear it", 403, BY + 58, MUTED, PANEL, 1)
        self.button("left", "<")
        self.button("right", ">")
        self.button("turn", "Turn")
        self.button("drop", "Drop")
        if self.over:
            self.panel("The van is full", (("Score %d" % self.score, TXT), ("Best %d" % self.best, GOLD),
                                           ("Tap to pack again · X to leave", TXT)))
        self.d.show()

    # -- the loop ----------------------------------------------------------------

    async def run(self, stop):
        if self.begin():
            self.new()
        dirty = True
        fall_at = time.ticks_ms()
        held, rep_at = None, 0
        while not stop():
            now = time.ticks_ms()
            ev = self.tp.poll()
            if ev:
                if ev[0] == "up":
                    held = None
                    if self.is_exit(ev[3], ev[4]):
                        return
                    if self.over:
                        self.new()
                        dirty = True
                elif ev[0] == "down" and not self.over:
                    x, y = ev[1], ev[2]
                    for name, (bx, by, bw, bh) in BTN.items():
                        if bx <= x < bx + bw and by <= y < by + bh:
                            if name == "turn":
                                self.turn()
                            elif name == "drop":
                                self.drop()
                            else:
                                self.move(-1 if name == "left" else 1)
                                held, rep_at = name, now + 220    # hold to keep sliding
                            dirty = True
                elif ev[0] == "hold" and held and time.ticks_diff(now, rep_at) >= 0:
                    self.move(-1 if held == "left" else 1)
                    rep_at = now + 80
                    dirty = True
            if not self.over and time.ticks_diff(now, fall_at) >= max(90, 720 - (self.level - 1) * 65):
                fall_at = now
                self.fall()
                dirty = True
            if dirty:
                self.draw()
                dirty = False
            await asyncio.sleep_ms(10)
