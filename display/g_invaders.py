# Van Invaders: the classic, defended by a campervan.
#
# Rows of invaders march side to side and down, dropping bombs. Slide a
# finger along the strip at the bottom: the van follows it and fires by itself
# while you touch (the panel reads one finger, so moving and firing are one
# gesture). Hide behind the three hedges; they wear away. Clear the sky for
# the next wave, a little faster and a little lower.

import asyncio
import random
import time

import framebuf

import gfx
from arcade import (Base, W, H, HUD_H, C, BG, PANEL, PANEL2, TXT, MUTED, GOLD, RED, GREEN, BLACK)

STRIP = (0, H - 44, W, 44)                     # the touch strip: slide to move
PLAY_B = H - 50                                 # the bottom of the play area
VAN_Y = PLAY_B - 20
KEY = C(255, 0, 255)
# 11 x 8 invaders, two poses each, drawn at double size
ALIENS = (
    (("  X     X  ", "   X   X   ", "  XXXXXXX  ", " XX XXX XX ", "XXXXXXXXXXX", "X XXXXXXX X", "X X     X X", "   XX XX   "),
     ("  X     X  ", "X  X   X  X", "X XXXXXXX X", "XXX XXX XXX", "XXXXXXXXXXX", " XXXXXXXXX ", "  X     X  ", " X       X ")),
    (("    XXX    ", " XXXXXXXXX ", "XXXXXXXXXXX", "XXX  X  XXX", "XXXXXXXXXXX", "   XX XX   ", "  XX X XX  ", "XX       XX"),
     ("    XXX    ", " XXXXXXXXX ", "XXXXXXXXXXX", "XXX  X  XXX", "XXXXXXXXXXX", "  XXX XXX  ", " XX     XX ", "  XX   XX  ")),
    (("   XXXXX   ", "  XXXXXXX  ", " XX XXX XX ", " XXXXXXXXX ", "   X   X   ", "  X XXX X  ", " X X   X X ", "           "),
     ("   XXXXX   ", "  XXXXXXX  ", " XX XXX XX ", " XXXXXXXXX ", "   X   X   ", "    XXX    ", "   X   X   ", "  X     X  ")))
ROW_KIND = (2, 0, 0, 1, 1)
ROW_COL = (C(245, 110, 200), C(120, 230, 120), C(120, 230, 120), C(90, 190, 255), C(90, 190, 255))
ROW_PTS = (40, 20, 20, 10, 10)
NCOLS, NROWS, AW, AH = 8, 5, 22, 16


def sprite(rows, col):
    w, h = len(rows[0]) * 2, len(rows) * 2
    buf = bytearray(w * h * 2)
    fb = framebuf.FrameBuffer(buf, w, h, framebuf.RGB565)
    fb.fill(KEY)
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch == "X":
                fb.fill_rect(x * 2, y * 2, 2, 2, col)
    return fb


class Game(Base):
    KEY = "invaders"
    TITLE = "Van Invaders"

    def sprites(self):
        self.spr = {}
        for r in range(NROWS):
            k = ROW_KIND[r]
            self.spr[r] = (sprite(ALIENS[k][0], ROW_COL[r]), sprite(ALIENS[k][1], ROW_COL[r]))
        # the stars behind it all, drawn once
        self.bgbuf = bytearray(W * H * 2)
        b = framebuf.FrameBuffer(self.bgbuf, W, H, framebuf.RGB565)
        b.fill(BG)
        for _ in range(60):
            b.pixel(random.getrandbits(9) % W, HUD_H + random.getrandbits(8) % (PLAY_B - HUD_H),
                    C(120, 115, 140) if random.getrandbits(1) else C(200, 195, 220))
        b.fill_rect(0, PLAY_B + 2, W, 2, C(60, 110, 60))                     # the ground
        x, y, w, h = STRIP
        gfx.rrect(b, x + 4, y + 4, w - 8, h - 8, 12, PANEL2)
        self.bg = b

    def wave(self):
        self.alive = [[True] * NCOLS for _ in range(NROWS)]
        self.fx = 40
        self.fy = HUD_H + 26 + min(60, (self.level - 1) * 12)
        self.fdir = 1
        self.pose = 0
        self.move_at = time.ticks_ms()
        self.bombs = []
        self.shot = None
        # three hedges of 5 x 3 blocks, 8 px each
        self.hedges = set()
        for hx in (70, 216, 362):
            for c in range(6):
                for r in range(3):
                    if not (r == 2 and 2 <= c <= 3):
                        self.hedges.add((hx + c * 8, PLAY_B - 58 + r * 8))
        self.ufo = None

    def new(self):
        self.score, self.lives, self.level = 0, 3, 1
        self.vx = W // 2
        self.over, self.started = False, False
        self.hit_at = None
        self.wave()

    def count(self):
        return sum(sum(1 for a in row if a) for row in self.alive)

    def alien_rect(self, r, c):
        return self.fx + c * (AW + 12), self.fy + r * (AH + 8)

    # -- one tick ------------------------------------------------------------------

    def tick(self, now, dt, finger):
        if finger is not None:                           # the van follows the finger
            dx = finger - self.vx
            self.vx += max(-6, min(6, dx))
            self.vx = max(18, min(W - 18, self.vx))
            if self.shot is None:
                self.shot = [self.vx, VAN_Y - 6]
                self.sfx("tap")
        if self.shot:
            self.shot[1] -= int(420 * dt)
            if self.shot[1] < HUD_H:
                self.shot = None
        # the formation marches, faster as it thins out
        n = self.count()
        step = max(60, int(40 + n * 12 - self.level * 20))
        if time.ticks_diff(now, self.move_at) >= step:
            self.move_at = now
            self.pose ^= 1
            left = min(c for r in range(NROWS) for c in range(NCOLS) if self.alive[r][c])
            right = max(c for r in range(NROWS) for c in range(NCOLS) if self.alive[r][c])
            nx = self.fx + self.fdir * 8
            if nx + left * (AW + 12) < 6 or nx + right * (AW + 12) + AW > W - 6:
                self.fdir = -self.fdir
                self.fy += 10
            else:
                self.fx = nx
            # a bomb, from the lowest invader of a random column
            if len(self.bombs) < 2 + self.level // 2 and random.getrandbits(2) == 0:
                cols = [c for c in range(NCOLS) if any(self.alive[r][c] for r in range(NROWS))]
                c = cols[random.getrandbits(4) % len(cols)]
                r = max(r for r in range(NROWS) if self.alive[r][c])
                x, y = self.alien_rect(r, c)
                self.bombs.append([x + AW // 2, y + AH])
        # a mothership across the top, now and then
        if self.ufo is None and random.getrandbits(9) == 0:
            self.ufo = [-30, 1] if random.getrandbits(1) else [W + 30, -1]
        if self.ufo:
            self.ufo[0] += self.ufo[1] * int(90 * dt + 1)
            if self.ufo[0] < -40 or self.ufo[0] > W + 40:
                self.ufo = None
        # the shot against the invaders, the mothership and the hedges
        if self.shot:
            sx, sy = self.shot
            for r in range(NROWS):
                for c in range(NCOLS):
                    if self.alive[r][c]:
                        x, y = self.alien_rect(r, c)
                        if x <= sx < x + AW and y <= sy < y + AH:
                            self.alive[r][c] = False
                            self.score += ROW_PTS[r]
                            self.shot = None
                            self.sfx("ok")
                            break
                if self.shot is None:
                    break
            if self.shot and self.ufo and abs(sx - self.ufo[0]) < 16 and HUD_H + 4 <= sy < HUD_H + 20:
                self.score += 100
                self.ufo = self.shot = None
                self.sfx("chime")
            if self.shot:
                for hb in self.hedges:
                    if hb[0] <= sx < hb[0] + 8 and hb[1] <= sy < hb[1] + 8:
                        self.hedges.discard(hb)
                        self.shot = None
                        break
        # the bombs
        for bm in self.bombs[:]:
            bm[1] += int(150 * dt + 1)
            hit_hedge = None
            for hb in self.hedges:
                if hb[0] <= bm[0] < hb[0] + 8 and hb[1] <= bm[1] < hb[1] + 8:
                    hit_hedge = hb
                    break
            if hit_hedge:
                self.hedges.discard(hit_hedge)
                self.bombs.remove(bm)
            elif VAN_Y - 4 <= bm[1] <= VAN_Y + 12 and abs(bm[0] - self.vx) < 16:
                self.bombs.remove(bm)
                self.lives -= 1
                self.hit_at = now
                self.sfx("bad")
            elif bm[1] > PLAY_B:
                self.bombs.remove(bm)
        # invaders reaching the hedges' line, or the last life gone: the end
        lowest = 0
        for r in range(NROWS):
            if any(self.alive[r]):
                lowest = r
        # the end: the last life gone, or the invaders down to the van
        if self.fy + lowest * (AH + 8) + AH >= VAN_Y - 4 or self.lives <= 0:
            self.over = True
            if self.record(self.score):
                self.sfx("chime")
        elif not self.count():
            self.level += 1
            self.sfx("chime")
            self.wave()

    # -- drawing -------------------------------------------------------------------

    def draw(self, now):
        fb, f = self.fb, self.f
        self.d.buf[:] = self.bgbuf
        self.hud("Score %d · wave %d" % (self.score, self.level), "Best %d" % self.best)
        for r in range(NROWS):
            s = self.spr[r][self.pose]
            for c in range(NCOLS):
                if self.alive[r][c]:
                    x, y = self.alien_rect(r, c)
                    fb.blit(s, x, y, KEY)
        for hx, hy in self.hedges:
            fb.fill_rect(hx, hy, 8, 8, C(50, 140, 60))
        if self.ufo:
            x = self.ufo[0]
            fb.ellipse(x, HUD_H + 12, 16, 6, RED, True)
            fb.ellipse(x, HUD_H + 8, 7, 5, C(255, 180, 180), True)
        for bx, by in self.bombs:
            fb.fill_rect(bx - 1, by - 6, 3, 8, C(255, 220, 120))
        if self.shot:
            fb.fill_rect(self.shot[0] - 1, self.shot[1] - 8, 3, 10, TXT)
        blink = self.hit_at is not None and time.ticks_diff(now, self.hit_at) < 900 and (now // 90) % 2
        if not blink:                                    # the van
            x = self.vx
            gfx.rrect(fb, x - 16, VAN_Y, 32, 14, 4, C(226, 110, 40))
            fb.fill_rect(x - 12, VAN_Y + 2, 24, 5, C(236, 232, 210))
            fb.fill_rect(x - 2, VAN_Y - 5, 4, 6, C(236, 232, 210))       # the roof gun
            fb.ellipse(x - 9, VAN_Y + 14, 3, 3, BLACK, True)
            fb.ellipse(x + 9, VAN_Y + 14, 3, 3, BLACK, True)
        for i in range(self.lives):                      # lives, bottom left of the strip
            gfx.rrect(fb, 14 + i * 22, STRIP[1] + 16, 16, 9, 3, C(226, 110, 40))
        f.sm.text(fb, "← slide here to move and fire →", W // 2, STRIP[1] + 13, MUTED, PANEL2, 1)
        if not self.started and not self.over:
            self.panel("Van Invaders", (("Slide along the strip at the bottom", TXT),
                                        ("The van moves and fires by itself", TXT), ("Touch the strip to start", GOLD)))
        if self.over:
            self.panel("Invaded!", (("Score %d · wave %d" % (self.score, self.level), TXT),
                                    ("Best %d" % self.best, GOLD), ("Touch the strip to play again", TXT)))
        self.d.show()

    async def run(self, stop):
        self.sprites()
        if self.begin():
            self.new()
        last = time.ticks_ms()
        finger = None
        try:
            while not stop():
                now = time.ticks_ms()
                dt = min(0.1, time.ticks_diff(now, last) / 1000)
                last = now
                ev = self.tp.poll()
                if ev:
                    if ev[0] in ("down", "hold"):
                        x, y = ev[1], ev[2]
                        if ev[0] == "down" and self.is_exit(x, y):
                            return
                        if y >= STRIP[1] - 20:
                            if self.over and ev[0] == "down":
                                self.new()
                            self.started = True
                            finger = x
                        else:
                            finger = None
                    else:
                        finger = None
                if self.started and not self.over:
                    self.tick(now, dt, finger)
                self.draw(now)
                await asyncio.sleep_ms(1)
        finally:
            self.bg = self.bgbuf = self.spr = None
