# Midge Swatter: a Highland evening by the loch, and the midges are out.
#
# Tap a midge to swat it. One left too long bites - five bites and you are
# back inside the van. They come faster and bite sooner as the score climbs.

import asyncio
import random
import time

import framebuf

import gfx
from arcade import (Base, W, H, HUD_H, C, BG, TXT, MUTED, GOLD, RED, BLACK)

LIVES = 5
WING = C(205, 210, 222)
BODY = C(20, 18, 22)


class Game(Base):
    KEY = "midge"
    TITLE = "Midge Swatter"

    def scenery(self):
        """Dusk over the loch, drawn once."""
        self.bgbuf = bytearray(W * H * 2)
        b = framebuf.FrameBuffer(self.bgbuf, W, H, framebuf.RGB565)
        top, mid = (40, 34, 70), (214, 120, 90)
        for y in range(HUD_H, 210, 4):                   # the sky, violet to sunset
            k = (y - HUD_H) / (210 - HUD_H)
            b.fill_rect(0, y, W, 4, C(int(top[0] + (mid[0] - top[0]) * k), int(top[1] + (mid[1] - top[1]) * k),
                                     int(top[2] + (mid[2] - top[2]) * k)))
        b.ellipse(360, 200, 26, 26, C(255, 200, 120), True)            # the sun, going down
        from array import array
        b.poly(0, 0, array("h", (0, 210, 90, 150, 170, 190, 250, 130, 340, 185, 420, 150, 480, 180, 480, 210)),
               C(40, 36, 60), True)                                    # the hills
        for y in range(210, H, 3):                                     # the loch
            b.fill_rect(0, y, W, 3, C(30, 40 + (y - 210) // 6, 70 + (y - 210) // 4))
        for x in range(300, 420, 9):                                   # the sun on the water
            b.hline(x, 216 + (x % 5) * 6, 18, C(230, 170, 110))
        self.bg = b

    def new(self):
        self.m = []                                     # [x, y, vx, vy, born ms]
        self.score, self.lives, self.over = 0, LIVES, False
        self.splats = []                                # (x, y, ms)
        self.bite_at = None
        self.spawn_at = time.ticks_ms()

    def spawn(self, now):
        x = 30 + random.getrandbits(9) % (W - 60)
        y = HUD_H + 30 + random.getrandbits(8) % (H - HUD_H - 60)
        self.m.append([x, y, (random.getrandbits(4) - 8) / 3, (random.getrandbits(4) - 8) / 3, now])

    def draw(self, now):
        fb, f = self.fb, self.f
        self.d.buf[:] = self.bgbuf
        self.hud("Swatted %d" % self.score, "Best %d" % self.best)
        for i in range(LIVES):                          # bites left, as hearts
            fb.ellipse(300 + i * 16, 16, 5, 5, RED if i < self.lives else C(70, 60, 70), True)
        flap = (now // 60) % 2
        life = self.life()
        for x, y, vx, vy, born in self.m:
            x, y = int(x), int(y)
            age = time.ticks_diff(now, born)
            # wings flap; the body reddens as it gets ready to bite
            fb.ellipse(x - 5, y - (4 if flap else 2), 6, 3 if flap else 2, WING, True)
            fb.ellipse(x + 5, y - (4 if flap else 2), 6, 3 if flap else 2, WING, True)
            k = min(1.0, age / life)
            fb.ellipse(x, y, 4, 3, gfx.blend(BODY, RED, k * k), True)
        for x, y, t in self.splats:
            fb.ellipse(x, y, 7, 5, C(120, 20, 20), True)
        if self.bite_at is not None and time.ticks_diff(now, self.bite_at) < 250:
            fb.rect(0, HUD_H, W, H - HUD_H, RED)
            fb.rect(1, HUD_H + 1, W - 2, H - HUD_H - 2, RED)
        if self.over:
            self.panel("Bitten!", (("Swatted %d" % self.score, TXT), ("Best %d" % self.best, GOLD),
                                   ("Tap to swat again · X to leave", TXT)))
        self.d.show()

    def life(self):
        return max(1500, 3600 - self.score * 25)       # ms before a midge bites

    async def run(self, stop):
        self.scenery()
        if self.begin():
            self.new()
        last = time.ticks_ms()
        try:
            while not stop():
                now = time.ticks_ms()
                dt = min(0.1, time.ticks_diff(now, last) / 1000)
                last = now
                ev = self.tp.poll()
                if ev and ev[0] == "down":
                    x, y = ev[1], ev[2]
                    if self.is_exit(x, y):
                        return
                    if self.over:
                        self.new()
                    else:
                        hit = False
                        for mm in self.m:
                            if (mm[0] - x) ** 2 + (mm[1] - y) ** 2 < 26 * 26:
                                self.m.remove(mm)
                                self.splats.append((int(mm[0]), int(mm[1]), now))
                                self.score += 1
                                hit = True
                                self.sfx("tap")
                                break
                        if not hit:
                            self.sfx("page")
                if not self.over:
                    if time.ticks_diff(now, self.spawn_at) >= 0:
                        self.spawn(now)
                        self.spawn_at = time.ticks_add(now, max(320, 1100 - self.score * 12))
                    life = self.life()
                    for mm in self.m[:]:
                        # a drifting, jittery flight, kept over the scene
                        mm[2] += (random.getrandbits(4) - 7.5) / 20
                        mm[3] += (random.getrandbits(4) - 7.5) / 20
                        mm[2], mm[3] = max(-3, min(3, mm[2])), max(-3, min(3, mm[3]))
                        mm[0] = max(12, min(W - 12, mm[0] + mm[2] * dt * 30))
                        mm[1] = max(HUD_H + 14, min(H - 12, mm[1] + mm[3] * dt * 30))
                        if time.ticks_diff(now, mm[4]) > life:
                            self.m.remove(mm)
                            self.lives -= 1
                            self.bite_at = now
                            self.sfx("bad")
                            if self.lives <= 0:
                                self.over = True
                                self.m = []
                                if self.record(self.score):
                                    self.sfx("chime")
                                break
                    self.splats = [s for s in self.splats if time.ticks_diff(now, s[2]) < 500]
                self.draw(now)
                await asyncio.sleep_ms(1)
        finally:
            self.bg = self.bgbuf = None
