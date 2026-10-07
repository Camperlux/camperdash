# Minesweeper: clear the field without setting off a mine.
#
# Tap a square to open it; a number says how many of its eight neighbours
# hide a mine. Press and hold to plant a flag where you are sure one is. Tap a
# number whose mines are all flagged and its other neighbours open at once.
# The first tap is always safe. Best: the fastest clear.

import asyncio
import random
import time

from arcade import (Base, W, H, HUD_H, C, BG, PANEL, PANEL2, TXT, MUTED, GOLD, RED, BLUE, GREEN)

COLS, ROWS = 16, 9
CELL = 28
GX = (W - COLS * CELL) // 2
GY = HUD_H + 8
MINES = 22
HOLD_MS = 400
NUM_COL = (None, BLUE, GREEN, RED, C(120, 90, 220), C(200, 90, 40), C(40, 170, 170), TXT, MUTED)


class Game(Base):
    KEY = "mines"
    TITLE = "Minesweeper"
    LOWER_BETTER = True

    def new(self):
        self.mine = [[False] * COLS for _ in range(ROWS)]
        self.open = [[False] * COLS for _ in range(ROWS)]
        self.flag = [[False] * COLS for _ in range(ROWS)]
        self.laid = False
        self.over = None          # "won" / "lost"
        self.boom = None
        self.t0 = None
        self.secs = 0

    def near(self, r, c):
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if (dr or dc) and 0 <= r + dr < ROWS and 0 <= c + dc < COLS:
                    yield r + dr, c + dc

    def count(self, r, c):
        return sum(1 for rr, cc in self.near(r, c) if self.mine[rr][cc])

    def lay(self, r0, c0):
        """The mines, after the first tap, never on or next to it."""
        keep = {(r0, c0)} | set(self.near(r0, c0))
        spots = [(r, c) for r in range(ROWS) for c in range(COLS) if (r, c) not in keep]
        for r, c in random.sample(spots, MINES) if hasattr(random, "sample") else self._sample(spots):
            self.mine[r][c] = True
        self.laid = True
        self.t0 = time.ticks_ms()

    def _sample(self, spots):
        out = []
        while len(out) < MINES:
            s = spots.pop(random.randrange(len(spots)))
            out.append(s)
        return out

    def reveal(self, r, c):
        if self.open[r][c] or self.flag[r][c]:
            return
        if self.mine[r][c]:
            self.over, self.boom = "lost", (r, c)
            self.sfx("bad")
            return
        todo = [(r, c)]
        while todo:                                   # an empty square opens its neighbours
            rr, cc = todo.pop()
            if self.open[rr][cc] or self.flag[rr][cc]:
                continue
            self.open[rr][cc] = True
            if self.count(rr, cc) == 0:
                todo.extend(self.near(rr, cc))
        self.check_won()

    def chord(self, r, c):
        """A number with all its mines flagged: open the rest round it."""
        n = self.count(r, c)
        if n and sum(1 for rr, cc in self.near(r, c) if self.flag[rr][cc]) == n:
            for rr, cc in list(self.near(r, c)):
                if not self.over:
                    self.reveal(rr, cc)

    def check_won(self):
        shut = sum(1 for r in range(ROWS) for c in range(COLS) if not self.open[r][c])
        if shut == MINES and not self.over:
            self.over = "won"
            self.secs = time.ticks_diff(time.ticks_ms(), self.t0) // 1000
            self.record(max(1, self.secs))
            self.sfx("chime")

    def tap(self, x, y, held):
        c, r = (x - GX) // CELL, (y - GY) // CELL
        if not (0 <= r < ROWS and 0 <= c < COLS):
            return
        if held:
            if not self.open[r][c]:
                self.flag[r][c] = not self.flag[r][c]
                self.sfx("page")
            return
        if not self.laid:
            self.lay(r, c)
        if self.open[r][c]:
            self.chord(r, c)
        else:
            self.reveal(r, c)
            if not self.over:
                self.sfx("tap")

    def draw(self):
        fb, f = self.fb, self.f
        flags = sum(1 for r in range(ROWS) for c in range(COLS) if self.flag[r][c])
        secs = self.secs if self.over or not self.t0 else time.ticks_diff(time.ticks_ms(), self.t0) // 1000
        self.hud("Mines %d  Time %d" % (MINES - flags, secs),
                 ("Best %ds" % self.best) if self.best else "Hold to flag")
        fb.fill_rect(0, HUD_H, W, H - HUD_H, BG)
        for r in range(ROWS):
            for c in range(COLS):
                x, y = GX + c * CELL, GY + r * CELL
                if self.open[r][c]:
                    fb.fill_rect(x, y, CELL - 1, CELL - 1, PANEL)
                    n = self.count(r, c)
                    if n:
                        f.md.text(fb, str(n), x + CELL // 2, y + 3, NUM_COL[n], PANEL, 1)
                else:
                    fb.fill_rect(x, y, CELL - 1, CELL - 1, PANEL2)
                    if self.flag[r][c]:
                        fb.vline(x + 10, y + 6, 16, TXT)
                        fb.fill_rect(x + 11, y + 6, 9, 7, RED)
                if self.over and self.mine[r][c] and not self.flag[r][c]:
                    fb.fill_rect(x, y, CELL - 1, CELL - 1, RED if (r, c) == self.boom else PANEL2)
                    fb.ellipse(x + CELL // 2, y + CELL // 2, 7, 7, TXT if (r, c) == self.boom else C(20, 20, 24), True)
        if self.over == "won":
            self.panel("Cleared!", (("%d seconds%s" % (self.secs, " - a new best!" if self.secs == self.best else ""), TXT),
                                    ("Tap to play again", MUTED)))
        elif self.over == "lost":
            f.md.text(fb, "Boom! Tap to try again", W // 2, H - 28, GOLD, BG, 1)
        self.d.show()

    async def run(self, stop):
        if self.begin():
            self.new()
        dirty = True
        held_done = False
        shown = -1
        while not stop():
            ev = self.tp.poll()
            if ev:
                if ev[0] == "down":
                    held_done = False
                elif ev[0] == "hold" and not held_done and ev[3] >= HOLD_MS and not self.over:
                    held_done = True                  # a flag; the lift that follows does nothing
                    self.tap(ev[1], ev[2], True)
                    dirty = True
                elif ev[0] == "up":
                    if self.is_exit(ev[3], ev[4]):
                        return
                    if self.over:
                        self.new()
                    elif not held_done:
                        self.tap(ev[3], ev[4], False)
                    dirty = True
            # the clock, once a second
            if self.t0 and not self.over:
                s = time.ticks_diff(time.ticks_ms(), self.t0) // 1000
                if s != shown:
                    shown = s
                    dirty = True
            if dirty:
                self.draw()
                dirty = False
            await asyncio.sleep_ms(20)
