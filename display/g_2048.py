# 2048: Camper edition. Swipe to slide every tile; two the same merge into the
# next size up - from a tent all the way to a Camperlux at 2048.

import asyncio
import random

import gfx
from arcade import (Base, W, H, HUD_H, C, BG, PANEL, PANEL2, TXT, MUTED, GOLD, BLACK)

N = 4
T, G = 64, 6                                   # tile and gap, px
SIZE = N * T + (N + 1) * G                     # 286
BX, BY = (W - SIZE) // 2, HUD_H + 1
NAMES = {2: "Tent", 4: "Tipi", 8: "Pop-up", 16: "Trailer", 32: "Caravan", 64: "Camper",
         128: "Crafter", 256: "Coach", 512: "RV", 1024: "Glamper", 2048: "Lux!", 4096: "Legend"}
COLS = {2: (238, 228, 218), 4: (237, 224, 200), 8: (242, 177, 121), 16: (245, 149, 99),
        32: (246, 124, 95), 64: (246, 94, 59), 128: (237, 207, 114), 256: (237, 204, 97),
        512: (237, 200, 80), 1024: (237, 197, 63), 2048: (211, 169, 74), 4096: (60, 58, 50)}


class Game(Base):
    KEY = "2048"
    TITLE = "2048: Camper"

    def new(self):
        self.b = [[0] * N for _ in range(N)]
        self.score, self.over, self.won = 0, False, False
        self.add()
        self.add()

    def add(self):
        free = [(r, c) for r in range(N) for c in range(N) if not self.b[r][c]]
        if free:
            r, c = free[random.getrandbits(8) % len(free)]
            self.b[r][c] = 4 if random.getrandbits(4) == 0 else 2

    def slide(self, d):
        """d: 0 left, 1 right, 2 up, 3 down. True if anything moved."""
        moved, gained = False, 0
        for i in range(N):
            if d < 2:
                line = self.b[i][:] if d == 0 else self.b[i][::-1]
            else:
                line = [self.b[r][i] for r in range(N)]
                if d == 3:
                    line.reverse()
            vals = [v for v in line if v]
            out = []
            k = 0
            while k < len(vals):
                if k + 1 < len(vals) and vals[k] == vals[k + 1]:
                    out.append(vals[k] * 2)
                    gained += vals[k] * 2
                    k += 2
                else:
                    out.append(vals[k])
                    k += 1
            out += [0] * (N - len(out))
            if out != line:
                moved = True
            if d == 1 or d == 3:
                out.reverse()
            if d < 2:
                self.b[i] = out
            else:
                for r in range(N):
                    self.b[r][i] = out[r]
        self.score += gained
        return moved, gained

    def stuck(self):
        for r in range(N):
            for c in range(N):
                v = self.b[r][c]
                if not v or (c + 1 < N and self.b[r][c + 1] == v) or (r + 1 < N and self.b[r + 1][c] == v):
                    return False
        return True

    def draw(self):
        fb, f = self.fb, self.f
        fb.fill(BG)
        self.hud("Score %d" % self.score, "Best %d" % self.best)
        gfx.rrect(fb, BX, BY, SIZE, SIZE, 12, C(58, 52, 44))
        for r in range(N):
            for c in range(N):
                x, y = BX + G + c * (T + G), BY + G + r * (T + G)
                v = self.b[r][c]
                if not v:
                    gfx.rrect(fb, x, y, T, T, 8, C(78, 70, 60))
                    continue
                rgb = COLS.get(v, (60, 58, 50))
                col = C(*rgb)
                gfx.rrect(fb, x, y, T, T, 8, col)
                ink = BLACK if v <= 4 or 128 <= v <= 1024 else C(255, 250, 240)
                if v < 1000:
                    f.lg.text(fb, "%d" % v, x + T // 2, y + 8, ink, col, 1)
                else:                                   # four figures: the smaller font
                    f.mdb.text(fb, "%d" % v, x + T // 2, y + 12, ink, col, 1)
                name = NAMES.get(v, "")
                nf = f.sm if f.sm.width(name) <= T - 6 else None
                if nf:
                    nf.text(fb, name, x + T // 2, y + T - 20, ink, col, 1)
        # either side: what the tiles are, and how to play
        f.sm.text(fb, "Swipe to", 12, BY + 20, MUTED, BG)
        f.sm.text(fb, "slide the", 12, BY + 40, MUTED, BG)
        f.sm.text(fb, "tiles", 12, BY + 60, MUTED, BG)
        f.sm.text(fb, "Two the", 12, BY + 110, MUTED, BG)
        f.sm.text(fb, "same make", 12, BY + 130, MUTED, BG)
        f.sm.text(fb, "the next", 12, BY + 150, MUTED, BG)
        f.sm.text(fb, "size up", 12, BY + 170, MUTED, BG)
        top = max(max(row) for row in self.b)
        f.sm.text(fb, "Biggest", W - 12, BY + 20, MUTED, BG, 2)
        f.mdb.text(fb, NAMES.get(top, "%d" % top), W - 12, BY + 42, GOLD, BG, 2)
        f.sm.text(fb, "Goal", W - 12, BY + 110, MUTED, BG, 2)
        f.sm.text(fb, "Camperlux", W - 12, BY + 132, GOLD, BG, 2)
        f.sm.text(fb, "at 2048", W - 12, BY + 158, MUTED, BG, 2)
        if self.over:
            self.panel("No more moves", (("Score %d" % self.score, TXT), ("Best %d" % self.best, GOLD),
                                         ("Tap to play again · X to leave", TXT)))
        elif self.won == 1:
            self.panel("Camperlux!", (("You made 2048", TXT), ("Tap to keep going", TXT)))
        self.d.show()

    async def run(self, stop):
        if self.begin():
            self.new()
        dirty = True
        while not stop():
            ev = self.tp.poll()
            if ev and ev[0] == "up":
                x0, y0, x1, y1 = ev[1], ev[2], ev[3], ev[4]
                if self.is_exit(x1, y1):
                    return
                if self.over:
                    self.new()
                elif self.won == 1:
                    self.won = 2                       # carry on past 2048
                else:
                    mx, my = x1 - x0, y1 - y0
                    if max(abs(mx), abs(my)) >= 30:
                        d = (1 if mx > 0 else 0) if abs(mx) >= abs(my) else (3 if my > 0 else 2)
                        moved, gained = self.slide(d)
                        if moved:
                            self.add()
                            self.sfx("ok" if gained >= 64 else "tap")
                            if not self.won and any(2048 in row for row in self.b):
                                self.won = 1
                                self.sfx("chime")
                            if self.stuck():
                                self.over = True
                                self.record(self.score)
                                self.sfx("bad")
                dirty = True
            if dirty:
                self.draw()
                dirty = False
            await asyncio.sleep_ms(15)
