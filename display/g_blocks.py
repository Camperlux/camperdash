# Falling blocks: the classic of shapes that fall into a well.
#
# Steer each shape as it falls, turn it, and fill whole rows to clear them;
# four at once is worth the most. Every ten rows the fall gets quicker. The
# controls are on the right, like the other games: < and > (hold to keep
# going), Turn, Down (faster) and Drop (all the way).

import asyncio
import random
import time

import gfx
from arcade import (Base, W, H, HUD_H, C, BG, PANEL, PANEL2, TXT, MUTED, GOLD)

COLS, ROWS = 10, 20
CELL = 14
BX, BY = 14, HUD_H + 4
SHAPES = (  # cells, colour
    (((0, 1), (1, 1), (2, 1), (3, 1)), C(80, 210, 230)),     # I
    (((1, 0), (2, 0), (1, 1), (2, 1)), C(240, 210, 60)),     # O
    (((1, 0), (0, 1), (1, 1), (2, 1)), C(170, 90, 230)),     # T
    (((1, 0), (2, 0), (0, 1), (1, 1)), C(80, 200, 100)),     # S
    (((0, 0), (1, 0), (1, 1), (2, 1)), C(235, 80, 70)),      # Z
    (((0, 0), (0, 1), (1, 1), (2, 1)), C(70, 120, 230)),     # J
    (((2, 0), (0, 1), (1, 1), (2, 1)), C(240, 150, 50)))     # L
LINE_PTS = (0, 100, 300, 500, 800)
BTN = {"left": (300, 140, 80, 60), "right": (390, 140, 80, 60),
       "turn": (300, 206, 80, 50), "down": (390, 206, 80, 50),
       "drop": (300, 262, 170, 50)}


class Game(Base):
    KEY = "blocks"
    TITLE = "Falling Blocks"

    def new(self):
        self.grid = [[None] * COLS for _ in range(ROWS)]
        self.score = self.lines = 0
        self.over = False
        self.next = random.randrange(len(SHAPES))
        self.spawn()

    @property
    def level(self):
        return self.lines // 10 + 1

    def spawn(self):
        self.kind = self.next
        self.next = random.randrange(len(SHAPES))
        self.cells = list(SHAPES[self.kind][0])
        self.px, self.py = 3, 0
        if not self.fits(self.cells, self.px, self.py):
            self.over = True
            self.record(self.score)
            self.sfx("bad")

    def fits(self, cells, px, py):
        for cx, cy in cells:
            x, y = px + cx, py + cy
            if x < 0 or x >= COLS or y >= ROWS:
                return False
            if y >= 0 and self.grid[y][x] is not None:
                return False
        return True

    def move(self, dx, dy):
        if self.fits(self.cells, self.px + dx, self.py + dy):
            self.px += dx
            self.py += dy
            return True
        return False

    def turn(self):
        if self.kind == 1:
            return                                    # the square
        # a quarter turn about the shape's middle, then nudged off a wall
        rot = [(2 - cy, cx) for cx, cy in self.cells]
        for kick in (0, -1, 1, -2, 2):
            if self.fits(rot, self.px + kick, self.py):
                self.cells, self.px = rot, self.px + kick
                self.sfx("tap")
                return

    def lock(self):
        col = SHAPES[self.kind][1]
        for cx, cy in self.cells:
            if self.py + cy >= 0:
                self.grid[self.py + cy][self.px + cx] = col
        full = [r for r in range(ROWS) if all(self.grid[r][c] is not None for c in range(COLS))]
        if full:
            for r in full:
                del self.grid[r]
                self.grid.insert(0, [None] * COLS)
            self.lines += len(full)
            self.score += LINE_PTS[len(full)] * self.level
            self.sfx("chime" if len(full) == 4 else "ok")
        self.spawn()

    def fall_ms(self):
        return max(90, 700 - (self.level - 1) * 60)

    def draw(self):
        fb, f = self.fb, self.f
        self.hud("Score %d" % self.score, "Level %d" % self.level)
        fb.fill_rect(0, HUD_H, W, H - HUD_H, BG)
        fb.fill_rect(BX - 2, BY - 2, COLS * CELL + 4, ROWS * CELL + 4, PANEL2)
        fb.fill_rect(BX, BY, COLS * CELL, ROWS * CELL, PANEL)
        for r in range(ROWS):
            for c in range(COLS):
                if self.grid[r][c] is not None:
                    fb.fill_rect(BX + c * CELL, BY + r * CELL, CELL - 1, CELL - 1, self.grid[r][c])
        if not self.over:
            col = SHAPES[self.kind][1]
            # where it would land, faintly
            gy = self.py
            while self.fits(self.cells, self.px, gy + 1):
                gy += 1
            for cx, cy in self.cells:
                if gy + cy >= 0:
                    fb.rect(BX + (self.px + cx) * CELL, BY + (gy + cy) * CELL, CELL - 1, CELL - 1, MUTED)
            for cx, cy in self.cells:
                if self.py + cy >= 0:
                    fb.fill_rect(BX + (self.px + cx) * CELL, BY + (self.py + cy) * CELL, CELL - 1, CELL - 1, col)
        # the next shape, and the tally
        ix = BX + COLS * CELL + 22
        f.sm.text(fb, "Next", ix, BY + 4, MUTED, BG)
        cells, col = SHAPES[self.next]
        for cx, cy in cells:
            fb.fill_rect(ix + cx * 12, BY + 28 + cy * 12, 11, 11, col)
        f.sm.text(fb, "Lines %d" % self.lines, ix, BY + 70, TXT, BG)
        f.sm.text(fb, "Best %d" % (self.best or 0), ix, BY + 92, GOLD, BG)
        for name, lab in (("left", "<"), ("right", ">"), ("turn", "Turn"), ("down", "Down"), ("drop", "Drop")):
            self.button(BTN[name], lab, font=self.f.md)
        if self.over:
            self.panel("Game over", (("Score %d, %d lines" % (self.score, self.lines), TXT),
                                     ("Tap to play again", MUTED)))
        self.d.show()

    async def run(self, stop):
        if self.begin():
            self.new()
        fall_at = time.ticks_ms()
        held, rep_at = None, 0
        dirty = True
        while not stop():
            now = time.ticks_ms()
            ev = self.tp.poll()
            if ev:
                if ev[0] == "down":
                    if self.is_exit(ev[1], ev[2]):
                        return
                    if self.over:
                        pass
                    else:
                        b = self.hit(BTN, ev[1], ev[2])
                        held = b if b in ("left", "right", "down") else None
                        rep_at = time.ticks_add(now, 180)
                        if b == "left":
                            self.move(-1, 0)
                        elif b == "right":
                            self.move(1, 0)
                        elif b == "down":
                            if not self.move(0, 1):
                                self.lock()
                            fall_at = now
                        elif b == "turn":
                            self.turn()
                        elif b == "drop":
                            while self.move(0, 1):
                                self.score += 1
                            self.lock()
                            fall_at = now
                        dirty = True
                elif ev[0] == "up":
                    held = None
                    if self.over and not self.is_exit(ev[3], ev[4]):
                        self.new()
                        dirty = True
                    elif self.is_exit(ev[3], ev[4]):
                        return
            # holding < > or Down keeps it going
            if held and not self.over and time.ticks_diff(now, rep_at) >= 0:
                rep_at = time.ticks_add(now, 60)
                if held == "down":
                    if not self.move(0, 1):
                        self.lock()
                    fall_at = now
                else:
                    self.move(-1 if held == "left" else 1, 0)
                dirty = True
            if not self.over and time.ticks_diff(now, fall_at) >= self.fall_ms():
                fall_at = now
                if not self.move(0, 1):
                    self.lock()
                dirty = True
            if dirty:
                self.draw()
                dirty = False
            await asyncio.sleep_ms(15)
