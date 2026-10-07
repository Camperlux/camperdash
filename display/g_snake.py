# Road Trip Snake: steer the camper round the map, towing ever more trailers.
#
# Fuel cans add a trailer. A campsite pops up now and then for a bonus - get
# there before it closes. Hitting the edge of the map or your own trailers
# ends the trip. Steer with the arrow cross on the right. The van comes in
# several skins, chosen on the start screen and remembered.

import asyncio
import random
import time
from array import array

import framebuf

import gfx
from arcade import (Base, W, H, HUD_H, C, BG, PANEL, PANEL2, TXT, MUTED, GOLD, RED, hi_get, hi_set)

CELL = 20
COLS, ROWS = 18, 14                                      # the map, on the left: 360 x 280
OX, OY = 0, HUD_H + 4
PX = OX + COLS * CELL + 2                                # the arrows, a cross on the right - for the thumb
GRASS, GRASS2 = C(52, 110, 56), C(46, 100, 50)
DIRS = ((1, 0), (0, 1), (-1, 0), (0, -1))
PAD = {"up": (PX + 30, 90, 56, 62), "left": (PX, 158, 56, 62),
       "right": (PX + 60, 158, 56, 62), "down": (PX + 30, 226, 56, 62)}
ARROW = {"left": "←", "up": "↑", "down": "↓", "right": "→"}
TURN = {"right": 0, "down": 1, "left": 2, "up": 3}
# name, the van, its windscreen, and the trailers (they take turns)
SKINS = (("Classic", C(226, 110, 40), C(40, 60, 90), (C(236, 232, 210), C(210, 205, 190))),
         ("Split-screen", C(52, 120, 200), C(236, 232, 210), (C(52, 120, 200), C(236, 232, 210))),
         ("Crafter", C(235, 235, 240), C(40, 60, 90), (C(150, 152, 160), C(200, 202, 208))),
         ("Rainbow", C(248, 81, 73), C(40, 40, 50),
          (C(245, 184, 61), C(63, 185, 80), C(88, 166, 255), C(180, 110, 220), C(248, 81, 73))),
         ("Stealth", C(40, 40, 46), C(211, 169, 74), (C(60, 60, 68), C(80, 80, 90))),
         ("Fire engine", C(220, 40, 40), C(240, 240, 240), (C(240, 200, 40), C(220, 40, 40))))
SKIN_BTN = (140, 200, 200, 44)


class Game(Base):
    KEY = "snake"
    TITLE = "Road Trip Snake"

    def scenery(self):
        """The map, drawn once: a chequer of fields and a few trees."""
        self.bgbuf = bytearray(W * H * 2)
        b = framebuf.FrameBuffer(self.bgbuf, W, H, framebuf.RGB565)
        b.fill(BG)
        for r in range(ROWS):
            for c in range(COLS):
                b.fill_rect(OX + c * CELL, OY + r * CELL, CELL, CELL, GRASS if (r + c) % 2 else GRASS2)
        for _ in range(7):
            # kept inside the map, clear of the arrows beside it
            x = OX + 6 + random.getrandbits(9) % (COLS * CELL - 12)
            y = OY + 6 + random.getrandbits(9) % (ROWS * CELL - 12)
            b.ellipse(x, y, 5, 5, C(34, 78, 38), True)
        self.bg = b

    def new(self):
        self.body = [(6, 5), (5, 5), (4, 5)]            # head first
        self.dir = self.want = 0
        self.score, self.over, self.started = 0, False, False
        self.step_ms = 170
        self.site = None                                 # (col, row, until ms)
        self.fuel = None
        self.fuel = self.free()

    def free(self):
        while True:
            p = (random.getrandbits(8) % COLS, random.getrandbits(8) % ROWS)
            if p not in self.body and p != self.fuel:
                return p

    def turn_to(self, d):
        if (d + 2) % 4 != self.dir:                      # never straight back on yourself
            self.want = d

    def step(self, now):
        self.dir = self.want
        dx, dy = DIRS[self.dir]
        hx, hy = self.body[0]
        nx, ny = hx + dx, hy + dy
        if not (0 <= nx < COLS and 0 <= ny < ROWS) or (nx, ny) in self.body[:-1]:
            self.over = True
            self.sfx("bad")
            if self.record(self.score):
                self.sfx("chime")
            return
        self.body.insert(0, (nx, ny))
        if (nx, ny) == self.fuel:
            self.score += 10
            self.step_ms = max(80, self.step_ms - 4)
            self.sfx("ok")
            self.fuel = self.free()
        else:
            self.body.pop()
        if self.site and (nx, ny) == self.site[:2]:
            self.score += 50
            self.sfx("chime")
            self.site = None
        if self.site and time.ticks_diff(now, self.site[2]) >= 0:
            self.site = None
        if not self.site and random.getrandbits(5) == 0:
            c, r = self.free()
            self.site = (c, r, time.ticks_add(now, 6000))

    def draw(self, now):
        fb, f = self.fb, self.f
        self.d.buf[:] = self.bgbuf
        self.hud("Score %d" % self.score, "Best %d" % self.best)
        name, van, glass, trailers = SKINS[self.skin]
        fx, fy = OX + self.fuel[0] * CELL, OY + self.fuel[1] * CELL
        gfx.rrect(fb, fx + 4, fy + 3, 12, 15, 3, RED)            # a jerrycan
        fb.fill_rect(fx + 7, fy + 1, 6, 3, C(160, 40, 40))
        if self.site:
            sx, sy = OX + self.site[0] * CELL, OY + self.site[1] * CELL
            left = time.ticks_diff(self.site[2], now)
            if left > 1500 or (now // 150) % 2:                   # flashes as it closes
                fb.poly(sx, sy, array("h", (10, 1, 19, 18, 1, 18)), C(236, 120, 40), True)
                fb.poly(sx, sy, array("h", (10, 7, 14, 18, 6, 18)), C(90, 40, 20), True)
        for i in range(len(self.body) - 1, 0, -1):               # the trailers
            c, r = self.body[i]
            gfx.rrect(fb, OX + c * CELL + 2, OY + r * CELL + 2, CELL - 4, CELL - 4, 4,
                      trailers[i % len(trailers)])
        c, r = self.body[0]                                      # the van, facing its way
        x, y = OX + c * CELL, OY + r * CELL
        gfx.rrect(fb, x + 1, y + 1, CELL - 2, CELL - 2, 5, van)
        dx, dy = DIRS[self.dir]
        fb.fill_rect(x + 5 + dx * 5, y + 5 + dy * 5, 10 - abs(dx) * 5, 10 - abs(dy) * 5, glass)
        for k, rect in PAD.items():
            self.button(rect, ARROW[k])
        if not self.started and not self.over:
            self.panel("Road Trip Snake", (("Collect fuel for more trailers", TXT),
                                           ("Press an arrow to drive", TXT)))
            self.button(SKIN_BTN, "Van: " + name, font=f.md)
        if self.over:
            self.panel("End of the road", (("Score %d · %d trailers" % (self.score, len(self.body) - 1), TXT),
                                           ("Best %d" % self.best, GOLD), ("An arrow to drive again · X to leave", TXT)))
        self.d.show()

    async def run(self, stop):
        self.skin = hi_get("snake_skin", 0) % len(SKINS)
        self.scenery()
        if self.begin():
            self.new()
        step_at = time.ticks_ms()
        drawn = 0
        try:
            while not stop():
                now = time.ticks_ms()
                ev = self.tp.poll()
                if ev and ev[0] == "down":
                    x, y = ev[1], ev[2]
                    if self.is_exit(x, y):
                        return
                    b = self.hit(PAD, x, y)
                    if b:
                        if self.over:
                            self.new()
                        self.turn_to(TURN[b])
                        if not self.started:
                            self.started = True
                            step_at = now
                        self.sfx("tap")
                    elif not self.started and self.hit({"s": SKIN_BTN}, x, y):
                        self.skin = (self.skin + 1) % len(SKINS)
                        hi_set("snake_skin", self.skin)
                        self.sfx("tap")
                if not self.over and self.started and time.ticks_diff(now, step_at) >= self.step_ms:
                    step_at = now
                    self.step(now)
                if time.ticks_diff(now, drawn) >= 40:
                    drawn = now
                    self.draw(now)
                await asyncio.sleep_ms(5)
        finally:
            self.bg = self.bgbuf = None
