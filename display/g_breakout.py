# Breakout: knock down the wall.
#
# Slide a finger anywhere below the wall and the bat follows it. The ball
# leaves the bat at an angle that depends on where it hits, and speeds up as
# the wall comes down. Three balls; clear the wall for the next, a little
# faster. Top rows are worth more.

import asyncio
import time

from arcade import (Base, W, H, HUD_H, C, BG, TXT, MUTED, GOLD, RED, GREEN, BLUE, AMBER)

COLS, ROWS = 10, 6
BW, BH = 44, 14
GAP = 3
WALL_X = (W - (COLS * BW + (COLS - 1) * GAP)) // 2
WALL_Y = HUD_H + 30
ROW_COL = (RED, C(236, 120, 60), AMBER, GOLD, GREEN, BLUE)
ROW_PTS = (7, 6, 5, 4, 3, 2)
BAT_W, BAT_H = 70, 10
BAT_Y = H - 28
BALL = 8


class Game(Base):
    KEY = "breakout"
    TITLE = "Breakout"

    def new(self):
        self.score = 0
        self.lives = 3
        self.level = 1
        self.over = False
        self.wall()
        self.batx = (W - BAT_W) / 2
        self.park()

    def wall(self):
        self.bricks = [[True] * COLS for _ in range(ROWS)]
        self.left = COLS * ROWS

    def park(self):
        """The ball sits on the bat until a tap sends it."""
        self.stuck = True
        self.vx, self.vy = 0, 0

    def launch(self):
        self.stuck = False
        sp = 230 + 25 * (self.level - 1)
        self.vx, self.vy = sp * 0.5, -sp

    def tick(self, dt):
        if self.stuck:
            self.bx = self.batx + BAT_W / 2 - BALL / 2
            self.by = BAT_Y - BALL - 1
            return
        # small steps, so a fast ball cannot pass through a brick
        n = max(1, int(max(abs(self.vx), abs(self.vy)) * dt / 5) + 1)
        for _ in range(n):
            self.step(dt / n)
            if self.stuck or self.over:
                return

    def step(self, dt):
        self.bx += self.vx * dt
        self.by += self.vy * dt
        if self.bx < 0:
            self.bx, self.vx = 0, abs(self.vx)
        elif self.bx + BALL > W:
            self.bx, self.vx = W - BALL, -abs(self.vx)
        if self.by < HUD_H:
            self.by, self.vy = HUD_H, abs(self.vy)
        # the bat
        if self.vy > 0 and BAT_Y - BALL <= self.by <= BAT_Y and \
                self.batx - BALL < self.bx < self.batx + BAT_W:
            off = ((self.bx + BALL / 2) - (self.batx + BAT_W / 2)) / (BAT_W / 2)
            sp = (self.vx * self.vx + self.vy * self.vy) ** 0.5
            self.vx = off * sp * 0.85
            self.vy = -max(0.45 * sp, (sp * sp - self.vx * self.vx) ** 0.5)
            self.by = BAT_Y - BALL
            self.sfx("tap")
        # lost
        if self.by > H:
            self.lives -= 1
            self.sfx("bad")
            if self.lives <= 0:
                self.over = True
                self.record(self.score)
            else:
                self.park()
            return
        # the bricks: the one the ball's centre is in
        cx, cy = self.bx + BALL / 2, self.by + BALL / 2
        c = int((cx - WALL_X) // (BW + GAP))
        r = int((cy - WALL_Y) // (BH + GAP))
        if 0 <= c < COLS and 0 <= r < ROWS and self.bricks[r][c]:
            self.bricks[r][c] = False
            self.left -= 1
            self.score += ROW_PTS[r]
            # which face it came through decides the bounce
            bx0 = WALL_X + c * (BW + GAP)
            by0 = WALL_Y + r * (BH + GAP)
            dx = min(abs(cx - bx0), abs(cx - (bx0 + BW)))
            dy = min(abs(cy - by0), abs(cy - (by0 + BH)))
            if dx < dy:
                self.vx = -self.vx
            else:
                self.vy = -self.vy
            self.vx *= 1.01
            self.vy *= 1.01
            self.sfx("page")
            if not self.left:
                self.level += 1
                self.sfx("chime")
                self.wall()
                self.park()

    def draw(self):
        fb, f = self.fb, self.f
        self.hud("Score %d" % self.score, "Level %d  Balls %d" % (self.level, self.lives))
        fb.fill_rect(0, HUD_H, W, H - HUD_H, BG)
        for r in range(ROWS):
            for c in range(COLS):
                if self.bricks[r][c]:
                    fb.fill_rect(WALL_X + c * (BW + GAP), WALL_Y + r * (BH + GAP), BW, BH, ROW_COL[r])
        fb.fill_rect(int(self.batx), BAT_Y, BAT_W, BAT_H, TXT)
        if not self.over:
            fb.fill_rect(int(self.bx), int(self.by), BALL, BALL, TXT)
        if self.over:
            self.panel("Game over", (("Score %d%s" % (self.score, " - a new best!" if self.score == self.best else ""), TXT),
                                     ("Tap to play again", MUTED)))
        elif self.stuck:
            f.md.text(fb, "Slide to move - tap to launch", W // 2, WALL_Y + ROWS * (BH + GAP) + 30, MUTED, BG, 1)
        self.d.show()

    async def run(self, stop):
        if self.begin():
            self.new()
        last = time.ticks_ms()
        while not stop():
            now = time.ticks_ms()
            dt = min(0.04, time.ticks_diff(now, last) / 1000)
            last = now
            ev = self.tp.poll()
            if ev:
                if ev[0] in ("down", "hold") and ev[2] > HUD_H + 6:
                    self.batx = max(0, min(W - BAT_W, ev[1] - BAT_W / 2))
                if ev[0] == "up":
                    if self.is_exit(ev[3], ev[4]):
                        return
                    if self.over:
                        self.new()
                    elif self.stuck:
                        self.launch()
            if not self.over:
                self.tick(dt)
            self.draw()
            await asyncio.sleep_ms(1)
