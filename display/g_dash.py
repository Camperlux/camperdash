# Campsite Dash: drive the camper down a country road to the campsite.
#
# Tap to hop - hold for a bigger one - over logs, cones and potholes; mud only
# slows you down. Later stages bring sheep in the lane and a height barrier,
# the one thing you must NOT hop under. Fuel runs down as you drive: pick up
# the jerry cans, some of them up in the air. Three dents and you are towed
# away. Reach the campsite for a bonus, then on to the next stage - a new
# scene each time, and a little faster.

import asyncio
import random
import time
from array import array

import framebuf

import gfx
from arcade import (Base, W, H, HUD_H, C, TXT, MUTED, GOLD, RED, GREEN, BLACK, PANEL2)

BASE_Y = 276                 # where wheels meet the road
ROAD_T, ROAD_B = 258, 296    # the road band
HILL_Y, HILL_H = 150, 100    # the parallax hills strip
CX = 70                      # the camper's left edge on screen
VW, VH = 58, 34              # the camper
KEY = C(255, 0, 255)         # transparent in the hills strip
GRAV, JUMP_V, HOLD_MS = 1500.0, 440.0, 230
DENTS = 3

# name, sky top, sky bottom, hills, grass, road
THEMES = (("Lakeside", (86, 156, 228), (200, 226, 246), (74, 140, 84), (96, 166, 76), (70, 70, 78)),
          ("Forest Track", (64, 124, 186), (170, 206, 226), (32, 88, 54), (66, 128, 56), (84, 70, 58)),
          ("Mountain Pass", (40, 34, 70), (214, 120, 90), (64, 58, 86), (74, 104, 64), (62, 60, 70)))

CREAM, STRIPE, GLASS = C(236, 232, 210), C(226, 110, 40), C(60, 92, 132)


def _lerp(a, b, k):
    return C(int(a[0] + (b[0] - a[0]) * k), int(a[1] + (b[1] - a[1]) * k), int(a[2] + (b[2] - a[2]) * k))


class Game(Base):
    KEY = "dash"
    TITLE = "Campsite Dash"

    # ---- scenery, drawn once per stage -----------------------------------------------
    def scenery(self):
        name, top, bot, hill, grass, road = THEMES[(self.stage - 1) % len(THEMES)]
        self.grass, self.roadc = C(*grass), C(*road)
        self.verge = C(max(0, grass[0] - 22), max(0, grass[1] - 26), max(0, grass[2] - 18))
        if self.skybuf is None:
            self.skybuf = bytearray(W * H * 2)
            self.hillbuf = bytearray(W * HILL_H * 2)
        s = framebuf.FrameBuffer(self.skybuf, W, H, framebuf.RGB565)
        for y in range(HUD_H, ROAD_T, 4):
            s.fill_rect(0, y, W, 4, _lerp(top, bot, (y - HUD_H) / (ROAD_T - HUD_H)))
        dusk = self.stage % 3 == 0
        s.ellipse(390, 80 if not dusk else 130, 22, 22, C(255, 236, 150) if not dusk else C(255, 200, 120), True)
        # a far range, still, behind the moving hills
        s.poly(0, 0, array("h", (0, 200, 70, 150, 150, 176, 230, 132, 320, 168, 400, 140, 480, 160, 480, 220, 0, 220)),
               _lerp(bot, hill, 0.45), True)
        s.fill_rect(0, ROAD_T - 38, W, H - ROAD_T + 38, self.grass)
        h = framebuf.FrameBuffer(self.hillbuf, W, HILL_H, framebuf.RGB565)
        h.fill(KEY)
        hc = C(*hill)
        # rolling hills that tile: the outline starts and ends at the same height
        pts = [0, HILL_H]
        for x in range(0, W + 1, 40):
            pts += [x, 40 + int(22 * ((x * 7 % 97) / 97)) if 0 < x < W else 52]
        pts += [W, HILL_H]
        h.poly(0, 0, array("h", pts), hc, True)
        if self.stage % 3 == 2:                                    # forest: fir trees
            for x in range(16, W, 46):
                ty = 30 + (x * 13 % 17)
                h.poly(x, ty, array("h", (0, 0, 12, 34, -12, 34)), C(24, 70, 44), True)
        elif self.stage % 3 == 0:                                  # mountains: snowy tops
            for x in (60, 220, 380):
                h.poly(x, 10, array("h", (0, 0, 40, 60, -40, 60)), C(80, 74, 104), True)
                h.poly(x, 10, array("h", (0, 0, 12, 18, -12, 18)), C(240, 240, 250), True)
        self.hills = h
        self.stage_name = name

    # ---- a game, and a stage ----------------------------------------------------------
    def new(self):
        self.score, self.dents, self.stage = 0, 0, 1
        self.over = self.ready = False
        self.ready = True                       # "tap to start"
        self.start_stage()

    def start_stage(self):
        self.scenery()
        self.dist = 0.0                         # along this stage, px
        self.length = 5200 + 1300 * (self.stage - 1)
        self.speed = 0.0
        self.base = 150 + 18 * (self.stage - 1)
        self.fuel = 100.0
        self.yoff, self.vy, self.hold_from = 0.0, 0.0, None
        self.obs = []                           # [kind, world x, width, extra]
        self.next_obs = 500.0
        self.next_fuel = 900.0
        self.hurt_until = 0
        self.slow_until = 0
        self.arrived = False
        self.clear = False                      # the stage-done panel
        self.t_stage = 0.0

    # ---- what comes down the road -----------------------------------------------------
    def spawn(self):
        k = self.stage
        kinds = ["log", "cone", "pothole", "puddle", "log", "cone"]
        if k >= 2:
            kinds += ["sheep", "barrier"]
        if k >= 3:
            kinds += ["sheep", "pothole", "cones"]
        kind = kinds[random.getrandbits(8) % len(kinds)]
        x = self.dist + W + 40
        w = {"log": 38, "cone": 16, "cones": 70, "pothole": 46, "puddle": 64, "sheep": 36, "barrier": 74}[kind]
        self.obs.append([kind, x, w, 0])
        gap = 230 + random.getrandbits(8) % max(60, 260 - 30 * k)
        if kind == "barrier":
            gap += 120                          # room to settle before and after it
        self.next_obs = self.dist + gap + w

    def spawn_fuel(self):
        up = random.getrandbits(1)
        self.obs.append(["fuel", self.dist + W + 20, 18, 58 if up else 0])
        self.next_fuel = self.dist + 1100 + random.getrandbits(9)

    # ---- collisions ---------------------------------------------------------------------
    def check(self, now):
        vx0, vx1 = CX + 4, CX + VW - 4
        bottom, top = self.yoff, self.yoff + VH
        for o in self.obs[:]:
            kind, ox, w, ex = o
            sx = ox - self.dist
            if sx + w < vx0 or sx > vx1:
                continue
            hit = False
            if kind == "fuel":
                if bottom < ex + 18 and top > ex:
                    self.fuel = min(100.0, self.fuel + 24)
                    self.score += 20
                    self.obs.remove(o)
                    self.sfx("tap")
                continue
            if kind == "puddle":
                if bottom < 2 and o[3] == 0:
                    o[3] = 1
                    self.slow_until = time.ticks_add(now, 900)
                    self.sfx("page")
                continue
            if kind == "pothole":
                # a wheel in the hole: only on the ground, over its middle
                hit = bottom < 2 and sx + 10 < vx1 - 6 and sx + w - 10 > vx0 + 6
            elif kind == "barrier":
                hit = top > 46                  # the bar is 46 px up: fine on the road
            else:
                h = {"log": 14, "cone": 22, "cones": 22, "sheep": 26}[kind]
                hit = bottom < h
            if hit and time.ticks_diff(now, self.hurt_until) >= 0:
                self.dents += 1
                self.hurt_until = time.ticks_add(now, 1300)
                self.speed *= 0.55
                self.sfx("bad")
                if kind != "barrier":
                    self.obs.remove(o)
                if self.dents >= DENTS:
                    self.end("Towed away!")
                return

    def end(self, why):
        self.over = why
        self.score += int(self.dist / 5)
        if self.record(self.score):
            self.sfx("chime")

    # ---- drawing ------------------------------------------------------------------------
    def draw_camper(self, now):
        fb = self.fb
        x, yb = CX, BASE_Y - int(self.yoff)
        if time.ticks_diff(self.hurt_until, now) > 0 and (now // 90) % 2:
            return                                                  # flashing after a knock
        gfx.rrect(fb, x + 4, yb - VH - 4, 34, 7, 3, C(210, 206, 186))            # pop-top roof
        gfx.rrect(fb, x, yb - VH + 2, VW, VH - 8, 7, CREAM)
        fb.fill_rect(x + 2, yb - 14, VW - 4, 5, STRIPE)
        fb.fill_rect(x + 8, yb - VH + 7, 13, 9, GLASS)                            # side windows
        fb.fill_rect(x + 25, yb - VH + 7, 13, 9, GLASS)
        fb.poly(x + 43, yb - VH + 5, array("h", (0, 0, 9, 0, 14, 12, 0, 12)), GLASS, True)   # windscreen
        fb.fill_rect(x + VW - 3, yb - 13, 4, 4, C(255, 220, 120))                 # headlamp
        spin = (int(self.dist) // 6) % 2
        for wx in (x + 13, x + VW - 13):
            fb.ellipse(wx, yb - 5, 7, 7, BLACK, True)
            fb.ellipse(wx, yb - 5, 3, 3, C(150, 150, 160), True)
            if spin:
                fb.hline(wx - 6, yb - 5, 12, C(60, 60, 66))
            else:
                fb.vline(wx, yb - 11, 12, C(60, 60, 66))

    def draw_obs(self, now):
        fb = self.fb
        for kind, ox, w, ex in self.obs:
            sx = int(ox - self.dist)
            if sx > W or sx + w < 0:
                continue
            if kind == "log":
                fb.fill_rect(sx, BASE_Y - 14, w, 14, C(120, 78, 42))
                fb.ellipse(sx + w - 2, BASE_Y - 7, 5, 7, C(196, 150, 96), True)
                fb.ellipse(sx + w - 2, BASE_Y - 7, 2, 3, C(140, 96, 56), True)
            elif kind in ("cone", "cones"):
                for cx in range(sx, sx + w - 8, 26) if kind == "cones" else (sx,):
                    fb.poly(cx, BASE_Y, array("h", (0, 0, 8, -22, 16, 0)), C(245, 120, 30), True)
                    fb.fill_rect(cx + 4, BASE_Y - 12, 8, 4, TXT)
            elif kind == "pothole":
                fb.ellipse(sx + w // 2, BASE_Y + 3, w // 2, 6, C(28, 26, 30), True)
            elif kind == "puddle":
                fb.ellipse(sx + w // 2, BASE_Y + 4, w // 2, 7, C(104, 76, 50), True)
                if ex:
                    fb.ellipse(sx + w // 2, BASE_Y - 6, 10, 4, C(150, 116, 80), True)
            elif kind == "sheep":
                step = (now // 160) % 2
                fb.ellipse(sx + 18, BASE_Y - 16, 16, 10, TXT, True)
                fb.ellipse(sx + 4, BASE_Y - 20, 6, 6, C(40, 36, 40), True)
                for lx in (sx + 10, sx + 26):
                    fb.fill_rect(lx + (step if lx == sx + 10 else -step), BASE_Y - 8, 3, 8, C(40, 36, 40))
            elif kind == "barrier":
                for px in (sx, sx + w - 5):
                    fb.fill_rect(px, BASE_Y - 70, 5, 70, C(150, 150, 160))
                for k in range(0, w, 12):
                    fb.fill_rect(sx + k, BASE_Y - 70 + 16, 12, 8, GOLD if (k // 12) % 2 else BLACK)
                gfx.rrect(fb, sx + w // 2 - 22, BASE_Y - 96, 44, 22, 4, GOLD)          # the height sign
                self.f.sm.text(fb, "2.2m", sx + w // 2, BASE_Y - 93, BLACK, GOLD, 1)
            elif kind == "fuel":
                y = BASE_Y - ex - 18
                gfx.rrect(fb, sx, y, 16, 18, 3, RED)
                fb.fill_rect(sx + 5, y - 3, 6, 4, C(160, 30, 30))
                fb.line(sx + 3, y + 4, sx + 12, y + 14, C(255, 190, 180))

    PARK = 80                  # the camper stops this far past the end of the stage

    def draw_campsite(self):
        """The sign over the pitch, the tent and the campfire beyond it -
        laid out round where the camper parks (sx: its right-hand end then)."""
        fb = self.fb
        sx = int(self.length + self.PARK - self.dist) + CX + VW
        if sx > W + 120:
            return
        fb.fill_rect(sx - 30, BASE_Y - 82, 4, 82, C(120, 90, 60))                 # the sign, on a post
        gfx.rrect(fb, sx - 76, BASE_Y - 104, 96, 26, 4, C(40, 110, 60))
        self.f.sm.text(fb, "Campsite", sx - 28, BASE_Y - 100, TXT, C(40, 110, 60), 1)
        fb.poly(sx + 40, BASE_Y, array("h", (0, 0, 30, -44, 60, 0)), C(226, 110, 40), True)     # a tent
        fb.poly(sx + 62, BASE_Y, array("h", (0, 0, 8, -18, 16, 0)), C(60, 40, 30), True)
        gfx.thick_line(fb, sx + 126, BASE_Y - 1, sx + 154, BASE_Y - 7, C(110, 72, 40), 4)   # the campfire's logs
        gfx.thick_line(fb, sx + 126, BASE_Y - 7, sx + 154, BASE_Y - 1, C(96, 62, 34), 4)
        flick = (time.ticks_ms() // 120) % 3
        fb.poly(sx + 140, BASE_Y - 8, array("h", (-10, 0, -4, -14 - flick * 2, 0, -24 - flick * 3, 5, -12, 10, 0)),
                C(255, 120, 30), True)
        fb.poly(sx + 140, BASE_Y - 8, array("h", (-5, 0, 0, -13 - flick * 2, 5, 0)), C(255, 214, 90), True)

    def draw(self, now):
        fb, f = self.fb, self.f
        self.d.buf[:] = self.skybuf
        off = int(self.dist * 0.3) % W                                          # the hills, slower
        fb.blit(self.hills, -off, HILL_Y, KEY)
        fb.blit(self.hills, W - off, HILL_Y, KEY)
        fb.fill_rect(0, ROAD_T - 38, W, 38, self.grass)
        post = int(self.dist * 0.8) % 60                                        # the fence, nearer
        fb.hline(0, ROAD_T - 22, W, C(150, 120, 80))
        for px in range(-post, W, 60):
            fb.fill_rect(px, ROAD_T - 30, 5, 30, C(130, 100, 64))
        fb.fill_rect(0, ROAD_T, W, ROAD_B - ROAD_T, self.roadc)
        dash = int(self.dist) % 50
        for dx in range(-dash, W, 50):
            fb.fill_rect(dx, ROAD_B - 5, 26, 3, C(230, 226, 210))
        fb.fill_rect(0, ROAD_B, W, H - ROAD_B, self.verge)
        self.draw_campsite()
        self.draw_obs(now)
        self.draw_camper(now)
        # the top bar: stage, distance to go, fuel, dents
        togo = max(0, int((self.length - self.dist) / 5))
        self.hud("Stage %d  %dm" % (self.stage, togo))
        fb.fill_rect(318, 10, 64, 12, PANEL2)
        fb.fill_rect(319, 11, int(62 * self.fuel / 100), 10, GREEN if self.fuel > 25 else RED)
        f.sm.text(fb, "F", 306, 9, TXT, C(22, 20, 27))
        for i in range(DENTS):
            gfx.rrect(fb, 392 + i * 11, 11, 9, 9, 2, STRIPE if i >= self.dents else C(70, 60, 70))
        if self.ready:
            self.panel("Campsite Dash", (("Tap to hop - hold for a big hop", TXT),
                                          ("Never hop under a height barrier!", GOLD),
                                          ("Grab fuel.  Tap to start", TXT)))
        elif self.clear:
            self.panel("You made it!", (("%s - stage %d done" % (self.stage_name, self.stage), TXT),
                                        ("Score %d   Best %d" % (self.score, max(self.best, self.score)), GOLD),
                                        ("Tap for the next stage", TXT)))
        elif self.over:
            self.panel(self.over, (("Score %d" % self.score, TXT), ("Best %d" % self.best, GOLD),
                                   ("Tap to drive again - X to leave", TXT)))
        self.d.show()

    # ---- the loop -----------------------------------------------------------------------
    def step(self, now, dt):
        if self.arrived:
            # roll up to the pitch and stop on it
            stop = self.length + self.PARK
            self.speed = max(40.0, min(self.speed, 2.2 * (stop - self.dist)))
            self.dist = min(stop, self.dist + self.speed * dt)
            if self.dist >= stop and not self.clear:
                self.speed = 0.0
                self.clear = True
                bonus = 250 + int(self.fuel * 2)
                self.score += bonus + int(self.length / 5)
                self.sfx("chime")
            return
        self.t_stage += dt
        target = self.base + 3.0 * self.t_stage
        if time.ticks_diff(self.slow_until, now) > 0:
            target *= 0.55
        self.speed += (target - self.speed) * min(1.0, dt * 2.5)
        self.dist += self.speed * dt
        self.fuel -= 3.4 * dt
        if self.fuel <= 0:
            self.fuel = 0
            self.end("Out of fuel!")
            return
        # the hop: held, it rises further
        if self.yoff > 0 or self.vy > 0:
            g = GRAV
            if self.hold_from is not None and time.ticks_diff(now, self.hold_from) < HOLD_MS and self.vy > 0:
                g = GRAV * 0.45
            self.vy -= g * dt
            self.yoff += self.vy * dt
            if self.yoff <= 0:
                self.yoff, self.vy = 0.0, 0.0
        for o in self.obs:
            if o[0] == "sheep":
                o[1] -= 26 * dt                                  # wandering into the lane
        self.obs = [o for o in self.obs if o[1] + o[2] - self.dist > -40]
        if self.dist >= self.length:
            self.arrived = True
            self.obs = []
            return
        # nothing new once the campsite is nearly in view; what is already on
        # the road still counts
        if self.dist < self.length - W - 200:
            if self.dist >= self.next_obs:
                self.spawn()
            if self.dist >= self.next_fuel:
                self.spawn_fuel()
        self.check(now)

    async def run(self, stop):
        self.skybuf = None
        if self.begin():
            self.new()
        last = time.ticks_ms()
        try:
            while not stop():
                now = time.ticks_ms()
                dt = min(0.06, time.ticks_diff(now, last) / 1000)
                last = now
                ev = self.tp.poll()
                if ev:
                    if ev[0] == "down":
                        if self.is_exit(ev[1], ev[2]):
                            return
                        if self.ready:
                            self.ready = False
                        elif self.over:
                            self.new()
                            self.ready = False
                        elif self.clear:
                            self.stage += 1
                            self.start_stage()
                        elif self.yoff == 0 and not self.arrived:
                            self.vy = JUMP_V
                            self.yoff = 0.1
                            self.hold_from = now
                            self.sfx("hop")
                    elif ev[0] == "up":
                        self.hold_from = None
                if not (self.ready or self.over or self.clear):
                    self.step(now, dt)
                self.draw(now)
                await asyncio.sleep_ms(1)
        finally:
            self.skybuf = self.hillbuf = self.hills = None
