# Camp Crossing: the logo page's Easter egg (tap the logo three times).
#
# Frogger, in a campsite: get the camper across two busy roads of campervans
# to a free pitch at the top. Five pitches filled is a level; each level the
# vans go faster. Tap where you want to walk - above, below or to either side
# of the camper.
#
# The game owns the screen while it runs: main.py stops redrawing the pages,
# pauses the data polling, and hands the touch panel over. The alarm and panic
# checks carry on, and any of them ends the game at once.

import asyncio
import json
import random
import time

import framebuf

import gfx

W, H, ROW = 480, 320, 32
HI_FILE = "game_hi.json"
KEY = gfx.rgb(255, 0, 255)                # transparent in the sprites


def C(r, g, b):
    return gfx.rgb(r, g, b)


# ---- colours -------------------------------------------------------------------
HUD = C(22, 20, 27)
GRASS = C(58, 122, 58)
GRASS_DK = C(40, 94, 44)
HEDGE = C(28, 74, 34)
PITCH = C(168, 146, 100)
PITCH_EDGE = C(128, 108, 72)
ROAD = C(52, 52, 58)
DASH = C(210, 210, 200)
KERB = C(150, 150, 150)
PAVE = C(112, 106, 100)
PAVE_LINE = C(92, 88, 84)
WHITE = C(240, 236, 228)
GOLD = C(211, 169, 74)
RED = C(230, 70, 60)
BLACK = C(0, 0, 0)

# the vans' paint: body, and the cream top of a classic two-tone camper
PAINTS = ((C(226, 110, 40), C(240, 230, 205)), (C(52, 120, 200), C(240, 236, 220)),
          (C(70, 150, 90), C(236, 232, 210)), (C(200, 60, 60), C(245, 240, 230)),
          (C(40, 160, 170), C(236, 236, 226)), (C(230, 190, 60), C(250, 246, 232)))

PITCH_X = (48, 144, 240, 336, 432)        # centres of the five pitches
# the six lanes: row, direction, base speed px/s
LANES = ((2, -1, 46), (3, 1, 64), (4, -1, 40), (6, 1, 52), (7, -1, 72), (8, 1, 36))
SPAN = W + 200                            # vans wrap round over this width


def _fb(w, h):
    buf = bytearray(w * h * 2)
    return framebuf.FrameBuffer(buf, w, h, framebuf.RGB565)


def _van(length, body, top, right):
    """A campervan from the side, facing right or left."""
    h = 26
    f = _fb(length, h)
    f.fill(KEY)
    glass = C(40, 60, 90)
    dark = BLACK
    f.fill_rect(2, 11, length - 4, 10, body)            # lower body
    f.fill_rect(3, 3, length - 6, 9, top)               # cream top
    f.fill_rect(5, 1, length - 10, 2, top)              # roof
    for x, y in ((2, 11), (length - 3, 11), (3, 3), (length - 4, 3)):
        f.pixel(x, y, KEY)                              # rounded corners
    if length > 70:
        f.fill_rect(16, 0, length - 44, 2, WHITE)       # a pop-top
    front = length - 16 if right else 5
    f.fill_rect(front, 4, 11, 7, glass)                 # windscreen
    x = 21 if right else 18
    while x + 11 < length - 18:                         # side windows
        f.fill_rect(x, 5, 9, 5, glass)
        x += 13
    f.hline(2, 11, length - 4, C(90, 80, 70))           # the two-tone line
    hx = length - 4 if right else 2
    f.fill_rect(hx, 14, 2, 3, C(255, 240, 150))         # headlight
    tx = 2 if right else length - 4
    f.fill_rect(tx, 14, 2, 3, C(220, 30, 30))           # tail light
    for wx in (13, length - 14):
        f.ellipse(wx, 21, 5, 5, dark, True)             # wheels
        f.ellipse(wx, 21, 2, 2, C(170, 170, 170), True)
    return f, length


def _person(step):
    """The camper walking up the screen, seen from behind, rucksack on."""
    f = _fb(20, 26)
    f.fill(KEY)
    skin, hair, shirt = C(236, 190, 150), C(90, 60, 40), C(50, 140, 200)
    f.ellipse(10, 5, 4, 4, skin, True)
    f.fill_rect(6, 1, 9, 3, hair)
    f.fill_rect(5, 10, 10, 9, shirt)
    f.fill_rect(3, 11, 2, 7, skin)                      # arms
    f.fill_rect(15, 11, 2, 7, skin)
    f.fill_rect(6, 10, 8, 8, C(230, 120, 40))          # rucksack
    f.hline(7, 13, 6, C(180, 90, 30))
    a, b = (7, 5) if step else (5, 7)                  # legs, striding
    f.fill_rect(6, 19, 3, a, C(60, 60, 80))
    f.fill_rect(11, 19, 3, b, C(60, 60, 80))
    f.fill_rect(6, 18 + a, 3, 2, BLACK)
    f.fill_rect(11, 18 + b, 3, 2, BLACK)
    return f


def _tent():
    from array import array
    f = _fb(40, 26)
    f.fill(KEY)
    f.poly(0, 0, array("h", (20, 1, 39, 25, 1, 25)), C(236, 120, 40), True)
    f.poly(0, 0, array("h", (20, 8, 28, 25, 12, 25)), C(90, 40, 20), True)
    f.line(20, 1, 20, 25, C(250, 200, 120))
    return f


class Game:
    def __init__(self, disp, fonts, touch, sound=None):
        self.d, self.fb, self.f = disp, disp.fb, fonts
        self.touch, self.sound = touch, sound
        self.hi = self._load_hi()
        self.bg = None
        self.crash = self.banner = None
        if sound:
            try:
                import sound as sm
                sound._sounds.setdefault("hop", sm._tone(((1500, 20),), 0.3))
            except Exception:
                pass

    # -- set-up ----------------------------------------------------------------

    def _load_hi(self):
        try:
            with open(HI_FILE) as fh:
                return int(json.load(fh).get("hi", 0))
        except (OSError, ValueError, AttributeError):
            return 0

    def _save_hi(self):
        try:
            with open(HI_FILE, "w") as fh:
                json.dump({"hi": self.hi}, fh)
        except OSError:
            pass

    def _build(self):
        """The scenery, drawn once; every frame starts from a copy of it."""
        self.bgbuf = bytearray(W * H * 2)
        b = framebuf.FrameBuffer(self.bgbuf, W, H, framebuf.RGB565)
        b.fill_rect(0, 0, W, ROW, HUD)
        # the campsite: pitches in a hedge
        b.fill_rect(0, ROW, W, ROW, HEDGE)
        for i in range(0, W, 12):
            b.ellipse(i + 6, ROW + 4, 7, 5, GRASS_DK, True)
        for cx in PITCH_X:
            b.fill_rect(cx - 26, ROW + 3, 52, ROW - 3, PITCH_EDGE)
            b.fill_rect(cx - 24, ROW + 5, 48, ROW - 5, PITCH)
        for top in (2, 6):                                  # two roads
            y0 = top * ROW
            b.fill_rect(0, y0, W, 3 * ROW, ROAD)
            b.hline(0, y0, W, KERB)
            b.hline(0, y0 + 1, W, KERB)
            b.hline(0, y0 + 3 * ROW - 1, W, KERB)
            for k in (1, 2):
                y = y0 + k * ROW
                for x in range(0, W, 32):
                    b.fill_rect(x + 6, y - 1, 16, 2, DASH)
        # the verge between them, with a few flowers
        b.fill_rect(0, 5 * ROW, W, ROW, GRASS)
        rnd = random.getrandbits
        for _ in range(40):
            x, y = rnd(9) % W, 5 * ROW + 4 + rnd(5) % 24
            b.fill_rect(x, y, 2, 2, (WHITE, GOLD, C(220, 120, 200))[rnd(2) % 3])
        # where the camper starts: the car park
        b.fill_rect(0, 9 * ROW, W, ROW, PAVE)
        for x in range(0, W, 48):
            b.vline(x, 9 * ROW + 4, ROW - 8, PAVE_LINE)
        self.bg = b
        # sprites
        self.vans = {}
        for i, (body, top) in enumerate(PAINTS):
            for length in (64, 88):
                for right in (True, False):
                    self.vans[(i, length, right)] = _van(length, body, top, right)
        self.walk = (_person(0), _person(1))
        self.tent = _tent()

    def _free(self):
        self.bg = self.bgbuf = self.vans = self.walk = self.tent = None

    # -- the game --------------------------------------------------------------

    def _new_game(self):
        self.score, self.lives, self.level = 0, 3, 1
        self.pitches = [False] * 5
        self._new_level()

    def _new_level(self):
        self.traffic = []
        k = 1 + (self.level - 1) * 0.18
        for row, dirn, speed in LANES:
            cars, x = [], random.getrandbits(8)
            n = 2 + (1 if self.level >= 3 and speed < 60 else 0)
            for _ in range(n):
                paint = random.getrandbits(8) % len(PAINTS)
                length = 88 if random.getrandbits(1) else 64
                cars.append([float(x), paint, length])
                x += SPAN // n + random.getrandbits(5)
            self.traffic.append((row, dirn, speed * k, cars))
        self._respawn()

    def _respawn(self):
        self.col, self.row, self.best = 7, 9, 9
        self.step = 0

    def _px(self):
        return self.col * ROW + 16

    def _move(self, tx, ty):
        dx, dy = tx - self._px(), ty - (self.row * ROW + 16)
        if abs(dy) >= abs(dx):
            if abs(dy) < 10:
                return
            self.row = max(1, min(9, self.row + (1 if dy > 0 else -1)))
        else:
            self.col = max(0, min(14, self.col + (1 if dx > 0 else -1)))
        self.step ^= 1
        self._sfx("hop")
        if self.row < self.best:
            self.best = self.row
            self.score += 10
        if self.row == 1:
            self._arrive()

    def _arrive(self):
        px = self._px()
        for i, cx in enumerate(PITCH_X):
            if abs(px - cx) <= 22 and not self.pitches[i]:
                self.pitches[i] = True
                self.score += 100
                self._sfx("ok")
                if all(self.pitches):
                    self.score += 500
                    self.level += 1
                    self.pitches = [False] * 5
                    self.banner = ("Level %d" % self.level, time.ticks_ms())
                    self._sfx("chime")
                    self._new_level()
                else:
                    self._respawn()
                return
        self._die("Pitch taken!" if any(abs(px - cx) <= 22 for cx in PITCH_X)
                  else "Into the hedge!")

    def _die(self, why):
        self.lives -= 1
        self.crash = (self._px(), self.row * ROW + 16, why, time.ticks_ms())
        self._sfx("bad")

    def _hit(self):
        if self.row not in (2, 3, 4, 6, 7, 8):
            return False
        px = self._px()
        for row, dirn, speed, cars in self.traffic:
            if row != self.row:
                continue
            for x, paint, length in cars:
                if x + 4 < px + 7 and px - 7 < x + length - 4:
                    return True
        return False

    def _sfx(self, name):
        if self.sound:
            try:
                self.sound.play(name)
            except Exception:
                pass

    # -- drawing ---------------------------------------------------------------

    def _draw(self, state, now_ms):
        fb, f = self.fb, self.f
        self.d.buf[:] = self.bgbuf                # the scenery
        for i, filled in enumerate(self.pitches):
            if filled:
                fb.blit(self.tent, PITCH_X[i] - 20, ROW + 4, KEY)
        for row, dirn, speed, cars in self.traffic:
            y = row * ROW + 3
            for x, paint, length in cars:
                spr = self.vans[(paint, length, dirn > 0)][0]
                fb.blit(spr, int(x), y, KEY)
        if state == "play":
            fb.blit(self.walk[self.step], self._px() - 10, self.row * ROW + 3, KEY)
        if self.crash:
            cx, cy, why, t0 = self.crash
            r = 6 + (time.ticks_diff(now_ms, t0) // 60) % 6
            fb.ellipse(cx, cy, r + 4, r + 4, RED, True)
            fb.ellipse(cx, cy, r, r, C(255, 210, 80), True)
            self._msg(why)
        if self.banner and time.ticks_diff(now_ms, self.banner[1]) < 1500:
            self._msg(self.banner[0])
        # the score line
        f.sm.text(fb, "Score %d" % self.score, 10, 9, WHITE, HUD)
        f.sm.text(fb, "Level %d" % self.level, 130, 9, GOLD, HUD)
        for i in range(self.lives):
            fb.blit(self.walk[0], 210 + i * 22, 3, KEY)
        f.sm.text(fb, "Best %d" % self.hi, 390, 9, C(160, 150, 170), HUD, 2)
        fb.fill_rect(W - 40, 3, 36, 26, C(60, 50, 60))
        f.md.text(fb, "X", W - 22, 5, WHITE, C(60, 50, 60), 1)

    def _msg(self, text):
        f, fb = self.f, self.fb
        w = f.md.width(text) + 28
        fb.fill_rect((W - w) // 2, 5 * ROW + 3, w, ROW - 6, HUD)
        f.md.text(fb, text, W // 2, 5 * ROW + 6, WHITE, HUD, 1)

    def _panel(self, lines, big):
        f, fb = self.f, self.fb
        x, y, w, h = 70, 70, 340, 190
        fb.fill_rect(x, y, w, h, HUD)
        fb.rect(x, y, w, h, GOLD)
        f.lg.text(fb, big, W // 2, y + 16, GOLD, HUD, 1)
        for i, (text, col) in enumerate(lines):
            f.sm.text(fb, text, W // 2, y + 66 + i * 24, col, HUD, 1)

    # -- the loop ----------------------------------------------------------------

    async def run(self, stop):
        """Play until X, or stop() says something needs the screen."""
        import gc
        gc.collect()
        self._build()
        gc.collect()
        self._new_game()
        self.crash = self.banner = None
        state = "title"
        was_down = True                       # the tap that started us is still on
        last = time.ticks_ms()
        idle_since = last
        try:
            while not stop():
                now = time.ticks_ms()
                dt = min(0.1, time.ticks_diff(now, last) / 1000)
                last = now
                # the vans keep driving on every screen
                for row, dirn, speed, cars in self.traffic:
                    for car in cars:
                        car[0] = (car[0] + dirn * speed * dt + 100) % SPAN - 100
                # a tap: where it went down
                p = self.touch.read()
                tap = p if (p and not was_down) else None
                was_down = bool(p)
                if tap:
                    idle_since = now
                    if tap[0] > W - 48 and tap[1] < ROW + 6:
                        break                                  # X: back to the logo
                if state == "title":
                    if tap:
                        self._new_game()
                        state = "play"
                elif state == "play":
                    if self.crash:
                        if time.ticks_diff(now, self.crash[3]) > 900:
                            self.crash = None
                            if self.lives <= 0:
                                state = "over"
                                if self.score > self.hi:
                                    self.hi = self.score
                                    self._save_hi()
                            else:
                                self._respawn()
                    else:
                        if tap:
                            self._move(tap[0], tap[1])
                        if not self.crash and self._hit():
                            self._die("Splat!")
                elif state == "over" and tap:
                    self._new_game()
                    state = "play"
                # nobody playing for two minutes: put the logo back
                if time.ticks_diff(now, idle_since) > 120000:
                    break
                self._draw(state, now)
                if state == "title":
                    self._panel((("Get the camper to a free pitch.", WHITE),
                                 ("Tap where you want to walk:", WHITE),
                                 ("above, below or beside them.", WHITE),
                                 ("Best %d  ·  tap to start" % self.hi, GOLD)),
                                "Camp Crossing")
                elif state == "over":
                    self._panel((("Score %d" % self.score, WHITE),
                                 ("Level %d" % self.level, WHITE),
                                 ("Best %d" % self.hi, GOLD),
                                 ("Tap to play again · X to leave", WHITE)),
                                "Game over")
                self.d.show()
                await asyncio.sleep_ms(1)
        finally:
            self._free()
            gc.collect()
