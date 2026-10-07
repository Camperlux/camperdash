# Pong: the first video game, for one player or two.
#
# Each bat follows a finger on its own half of the screen - the panel reads
# two fingers at once, so two people can play on one display. Alone, you have
# the right-hand bat and the display plays the left. The ball speeds up with
# every return and leaves the bat at an angle that depends on where it hit.
# First to 7.

import asyncio
import time

from arcade import (Base, W, H, HUD_H, BG, PANEL2, TXT, MUTED, GOLD, BLUE, RED)

TOP = HUD_H + 4
BOT = H - 4
BAT_W, BAT_H = 10, 58
LX, RX = 16, W - 16 - BAT_W
BALL = 10
TO_WIN = 7


class Game(Base):
    KEY = "pong"
    TITLE = "Pong"

    def new(self):
        self.score = [0, 0]
        self.ly = self.ry = (TOP + BOT - BAT_H) / 2
        self.over = None
        self.serve(1)

    def serve(self, to):
        """The ball from the middle, towards `to` (0 left, 1 right), after a
        moment's pause."""
        self.bx, self.by = (W - BALL) / 2, (TOP + BOT - BALL) / 2
        self.vx = 250 if to else -250
        self.vy = 90 if time.ticks_ms() & 1 else -90
        self.wait = 900

    def cpu(self, dt):
        """The display's bat: follows the ball, but only so fast, and only
        once it is coming its way - beatable."""
        target = (self.by + BALL / 2 - BAT_H / 2) if self.vx < 0 else (TOP + BOT - BAT_H) / 2
        step = 260 * dt
        d = target - self.ly
        self.ly += max(-step, min(step, d))

    def tick(self, dt):
        if self.wait > 0:
            self.wait -= dt * 1000
            return
        self.bx += self.vx * dt
        self.by += self.vy * dt
        if self.by < TOP:
            self.by, self.vy = TOP, abs(self.vy)
        elif self.by + BALL > BOT:
            self.by, self.vy = BOT - BALL, -abs(self.vy)
        # the bats: a hit sends it back faster, angled by where it struck
        for side, bx, by in ((0, LX, self.ly), (1, RX, self.ry)):
            coming = self.vx < 0 if side == 0 else self.vx > 0
            if not coming:
                continue
            if (bx - BALL <= self.bx <= bx + BAT_W) and by - BALL < self.by < by + BAT_H:
                off = ((self.by + BALL / 2) - (by + BAT_H / 2)) / (BAT_H / 2)
                sp = min(620, abs(self.vx) * 1.06 + 10)
                self.vx = sp if side == 0 else -sp
                self.vy = off * 330
                self.bx = bx + BAT_W if side == 0 else bx - BALL
                self.sfx("tap")
        if self.bx < -BALL or self.bx > W:
            win = 1 if self.bx < 0 else 0
            self.score[win] += 1
            self.sfx("ok" if (self.solo and win == 1) or not self.solo else "bad")
            if self.score[win] >= TO_WIN:
                self.over = win
                if self.solo and win == 1:
                    self.wins += 1
                    self.record(self.wins)
                self.sfx("chime")
            else:
                self.serve(win)

    def draw(self):
        fb, f = self.fb, self.f
        mode = "1 player" if self.solo else "2 players"
        self.hud("%d - %d" % (self.score[0], self.score[1]), mode)
        fb.fill_rect(0, HUD_H, W, H - HUD_H, BG)
        for y in range(TOP, BOT, 16):
            fb.fill_rect(W // 2 - 1, y, 3, 8, PANEL2)
        f.xl.text(fb, str(self.score[0]), W // 2 - 40, TOP + 6, MUTED, BG, 2)
        f.xl.text(fb, str(self.score[1]), W // 2 + 40, TOP + 6, MUTED, BG, 0)
        fb.fill_rect(LX, int(self.ly), BAT_W, BAT_H, BLUE if self.solo else RED)
        fb.fill_rect(RX, int(self.ry), BAT_W, BAT_H, GOLD)
        if self.over is None:
            fb.fill_rect(int(self.bx), int(self.by), BALL, BALL, TXT)
        if self.over is not None:
            who = ("You win!" if self.over == 1 else "The display wins") if self.solo \
                else ("Right wins!" if self.over == 1 else "Left wins!")
            self.panel(who, (("%d - %d" % (self.score[0], self.score[1]), TXT),
                             ("Tap for another game", MUTED)))
        elif self.wait > 0 and self.score == [0, 0]:
            msg = "Your bat: right side" if self.solo else "A finger each side"
            f.md.text(fb, msg, W // 2, H - 60, MUTED, BG, 1)
        self.d.show()

    def choose(self):
        """One player or two - asked at the start."""
        fb, f = self.fb, self.f
        self.hud("", "")
        fb.fill_rect(0, HUD_H, W, H - HUD_H, BG)
        f.lg.text(fb, "Pong", W // 2, HUD_H + 28, GOLD, BG, 1)
        rects = {"1": (60, 140, 170, 90), "2": (250, 140, 170, 90)}
        self.button(rects["1"], "1 player")
        self.button(rects["2"], "2 players")
        f.sm.text(fb, "First to %d" % TO_WIN, W // 2, 250, MUTED, BG, 1)
        self.d.show()
        return rects

    async def run(self, stop):
        if self.begin():
            self.wins = self.best or 0
            self.solo = None
        rects = None
        last = time.ticks_ms()
        while not stop():
            now = time.ticks_ms()
            dt = min(0.05, time.ticks_diff(now, last) / 1000)
            last = now
            ev = self.tp.poll()
            if ev and ev[0] == "up" and self.is_exit(ev[3], ev[4]):
                return
            if self.solo is None:
                if rects is None:
                    rects = self.choose()
                if ev and ev[0] == "up":
                    k = self.hit(rects, ev[3], ev[4])
                    if k:
                        self.solo = k == "1"
                        self.new()
                await asyncio.sleep_ms(20)
                continue
            if self.over is not None:
                if ev and ev[0] == "up":
                    self.new()
            else:
                # each finger moves the bat on its side of the screen
                for p in self.touch.read_all():
                    if p[1] < HUD_H + 6:
                        continue
                    y = max(TOP, min(BOT - BAT_H, p[1] - BAT_H / 2))
                    if p[0] >= W // 2:
                        self.ry = y
                    elif not self.solo:
                        self.ly = y
                if self.solo:
                    self.cpu(dt)
                self.tick(dt)
            self.draw()
            await asyncio.sleep_ms(1)
