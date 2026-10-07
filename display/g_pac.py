# Pac-Camper: munch every dot in the campsite maze, and keep away from the
# seagulls. The big glowing dots are BBQ sausages: eat one and for a few
# seconds the gulls turn blue and flee - catch one then for a bonus. Steer
# with the arrows on the right; a turn pressed early is kept until it fits.

import asyncio
import random
import time
from array import array

import framebuf

import gfx
from arcade import (Base, W, H, HUD_H, C, BG, PANEL2, TXT, MUTED, GOLD, RED, BLACK)

MAZE = ("#################",
        "#o.....#.#.....o#",
        "#.###.#...#.###.#",
        "#...............#",
        "#.##.#.###.#.##.#",
        "#....#..G..#....#",
        "#.#...#GGG#...#.#",
        "#.#.#.#####.#.#.#",
        "#...............#",
        "#.##.###.###.##.#",
        "#o..#...P...#..o#",
        "#.#...#.#.#...#.#",
        "#################")
ROWS, COLS, CELL = len(MAZE), len(MAZE[0]), 20
OX, OY = 6, HUD_H + 8                                    # the maze on the left, the arrows on the right
PX = OX + COLS * CELL + 4
DIRS = ((1, 0), (0, 1), (-1, 0), (0, -1))
PAD = {"up": (PX + 34, 96, 58, 58), "left": (PX, 158, 58, 58), "right": (PX + 68, 158, 58, 58),
       "down": (PX + 34, 220, 58, 58)}
ARROW = {"left": "←", "up": "↑", "down": "↓", "right": "→"}
TURN = {"right": 0, "down": 1, "left": 2, "up": 3}
WALL, WALL_EDGE = C(28, 36, 110), C(70, 90, 220)
GULLS = (C(240, 240, 245), C(250, 170, 60), C(120, 220, 250))
SCARED = C(60, 90, 230)
YELLOW = C(245, 205, 40)


class Game(Base):
    KEY = "pac"
    TITLE = "Pac-Camper"

    def wall(self, c, r):
        return not (0 <= r < ROWS and 0 <= c < COLS) or MAZE[r][c] == "#"

    def build(self):
        """The maze with its dots, drawn once; an eaten dot is painted out of
        this copy, so a frame is a copy of it plus the moving pieces."""
        self.bgbuf = bytearray(W * H * 2)
        b = framebuf.FrameBuffer(self.bgbuf, W, H, framebuf.RGB565)
        b.fill(BG)
        self.dots = set()
        self.big = set()
        for r in range(ROWS):
            for c in range(COLS):
                x, y = OX + c * CELL, OY + r * CELL
                ch = MAZE[r][c]
                if ch == "#":
                    b.fill_rect(x, y, CELL, CELL, WALL)
                    # a lit edge wherever the wall meets a path
                    if not self.wall(c, r - 1):
                        b.hline(x, y, CELL, WALL_EDGE)
                    if not self.wall(c, r + 1):
                        b.hline(x, y + CELL - 1, CELL, WALL_EDGE)
                    if not self.wall(c - 1, r):
                        b.vline(x, y, CELL, WALL_EDGE)
                    if not self.wall(c + 1, r):
                        b.vline(x + CELL - 1, y, CELL, WALL_EDGE)
                elif ch == ".":
                    self.dots.add((c, r))
                    b.fill_rect(x + 8, y + 8, 4, 4, C(240, 220, 180))
                elif ch == "o":
                    self.big.add((c, r))
        self.bg = b
        self.total = len(self.dots) + len(self.big)

    def reset_pieces(self):
        for r in range(ROWS):
            for c in range(COLS):
                if MAZE[r][c] == "P":
                    self.pac = [c, r]
        self.prev = list(self.pac)
        self.dir, self.want = 2, 2
        starts = [(c, r) for r in range(ROWS) for c in range(COLS) if MAZE[r][c] == "G"]
        self.gulls = [{"pos": list(starts[i]), "prev": list(starts[i]), "dir": 3, "home": starts[i],
                       "wait": 12 + i * 10} for i in range(3)]
        self.scared_until = 0
        self.moved_at = time.ticks_ms()

    def new(self, keep_score=False):
        if not keep_score:
            self.score, self.lives, self.level = 0, 3, 1
        self.build()
        self.reset_pieces()
        self.over, self.started, self.dying = False, False, None

    # -- moving --------------------------------------------------------------------

    def step_pac(self, now):
        c, r = self.pac
        for d in (self.want, self.dir):              # the turn asked for, if it fits yet
            dx, dy = DIRS[d]
            if not self.wall(c + dx, r + dy):
                self.dir = d
                self.prev = [c, r]
                self.pac = [c + dx, r + dy]
                break
        else:
            self.prev = [c, r]                       # against a wall: stopped
        p = tuple(self.pac)
        if p in self.dots:
            self.dots.discard(p)
            self.score += 10
            self.bg.fill_rect(OX + p[0] * CELL + 8, OY + p[1] * CELL + 8, 4, 4, BG)
        elif p in self.big:
            self.big.discard(p)
            self.score += 50
            self.scared_until = time.ticks_add(now, max(3000, 7000 - self.level * 700))
            self.sfx("ok")
        if not self.dots and not self.big:           # cleared: the next site, faster
            self.level += 1
            self.sfx("chime")
            self.new(keep_score=True)
            self.started = True

    def step_gull(self, g, now):
        if g["wait"] > 0:                            # out of the nest one by one
            g["wait"] -= 1
            g["prev"] = list(g["pos"])
            return
        c, r = g["pos"]
        scared = time.ticks_diff(self.scared_until, now) > 0
        options = []
        for d in range(4):
            if d == (g["dir"] + 2) % 4:
                continue                             # gulls do not turn straight back
            dx, dy = DIRS[d]
            if not self.wall(c + dx, r + dy):
                options.append(d)
        if not options:
            options = [(g["dir"] + 2) % 4]
        px, py = self.pac
        if scared or random.getrandbits(3) == 0:
            d = options[random.getrandbits(4) % len(options)]
        else:                                        # the way that gets closest to the camper
            d = min(options, key=lambda d: (c + DIRS[d][0] - px) ** 2 + (r + DIRS[d][1] - py) ** 2)
        g["dir"] = d
        g["prev"] = [c, r]
        g["pos"] = [c + DIRS[d][0], r + DIRS[d][1]]

    def collide(self, now):
        scared = time.ticks_diff(self.scared_until, now) > 0
        for g in self.gulls:
            same = g["pos"] == self.pac or (g["pos"] == self.prev and g["prev"] == self.pac)
            if not same:
                continue
            if scared:
                self.score += 200
                g["pos"], g["prev"], g["wait"] = list(g["home"]), list(g["home"]), 8
                self.sfx("chime")
            else:
                self.lives -= 1
                self.sfx("bad")
                self.dying = now
                return

    # -- drawing -------------------------------------------------------------------

    def at(self, cur, prev, k):
        return (OX + int((prev[0] + (cur[0] - prev[0]) * k) * CELL) + CELL // 2,
                OY + int((prev[1] + (cur[1] - prev[1]) * k) * CELL) + CELL // 2)

    def draw(self, now, k):
        fb, f = self.fb, self.f
        self.d.buf[:] = self.bgbuf
        self.hud("Score %d" % self.score, "Best %d" % self.best)
        f.sm.text(fb, "Level %d" % self.level, PX + 4, HUD_H + 10, GOLD, BG)
        for i in range(3):
            fb.ellipse(PX + 10 + i * 20, HUD_H + 42, 7, 7, YELLOW if i < self.lives else PANEL2, True)
        flash = (now // 200) % 2
        for (c, r) in self.big:                                    # the sausages
            if flash:
                fb.ellipse(OX + c * CELL + 10, OY + r * CELL + 10, 6, 6, C(230, 110, 60), True)
        scared = time.ticks_diff(self.scared_until, now)
        for i, g in enumerate(self.gulls):
            x, y = self.at(g["pos"], g["prev"], k)
            col = GULLS[i] if scared <= 0 or (scared < 1200 and flash) else SCARED
            fb.ellipse(x, y - 1, 8, 7, col, True)                  # a round gull
            fb.fill_rect(x - 8, y - 1, 17, 8, col)
            for fx in (-6, -1, 4):                                 # ragged tail feathers
                fb.fill_rect(x + fx, y + 7, 3, 2, BG)
            fb.fill_rect(x - 4, y - 3, 3, 3, BLACK)                # eyes
            fb.fill_rect(x + 2, y - 3, 3, 3, BLACK)
            fb.fill_rect(x - 1, y + 1, 3, 2, C(245, 170, 40))       # beak
        x, y = self.at(self.pac, self.prev, k)
        fb.ellipse(x, y, 9, 9, YELLOW, True)
        mouth = 7 if (now // 110) % 2 else 2
        dx, dy = DIRS[self.dir]
        fb.poly(x, y, array("h", (0, 0, dx * 11 - dy * mouth, dy * 11 - dx * mouth,
                                  dx * 11 + dy * mouth, dy * 11 + dx * mouth)), BG, True)
        for name, rect in PAD.items():
            self.button(rect, ARROW[name])
        if not self.started and not self.over:
            self.panel("Pac-Camper", (("Eat every dot; dodge the gulls", TXT),
                                      ("Sausages make them flee", TXT), ("Press an arrow to start", GOLD)))
        if self.over:
            self.panel("Game over", (("Score %d · level %d" % (self.score, self.level), TXT),
                                     ("Best %d" % self.best, GOLD), ("An arrow to play again · X to leave", TXT)))
        self.d.show()

    async def run(self, stop):
        if self.begin():
            self.new()
        step_ms = 190
        last = time.ticks_ms()
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
                        self.want = TURN[b]
                        if not self.started:
                            self.started = True
                            last = now
                if self.dying is not None:
                    if time.ticks_diff(now, self.dying) > 1200:
                        self.dying = None
                        if self.lives <= 0:
                            self.over = True
                            if self.record(self.score):
                                self.sfx("chime")
                        else:
                            self.reset_pieces()
                            last = now
                elif self.started and not self.over:
                    step_ms = max(110, 190 - (self.level - 1) * 15)
                    if time.ticks_diff(now, last) >= step_ms:
                        last = now
                        self.step_pac(now)
                        self.collide(now)
                        if self.dying is None:
                            scared = time.ticks_diff(self.scared_until, now) > 0
                            for g in self.gulls:
                                # scared gulls are slower: they move every other step
                                if not scared or (now // step_ms) % 2:
                                    self.step_gull(g, now)
                                else:
                                    g["prev"] = list(g["pos"])
                            self.collide(now)
                k = min(1.0, time.ticks_diff(now, last) / step_ms) if self.started and self.dying is None else 1.0
                self.draw(now, k)
                await asyncio.sleep_ms(1)
        finally:
            self.bg = self.bgbuf = None
