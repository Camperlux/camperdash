# Campsite Memory: turn the cards over two at a time and find the pairs.
#
# Sixteen cards, eight pictures from the campsite. A pair stays face up; a
# miss turns back after a moment. Fewest moves wins, so the best score here
# is the lowest.

import asyncio
import random
import time

import gfx
import icons
from aa import AA
from arcade import (Base, W, H, HUD_H, C, BG, PANEL, PANEL2, TXT, MUTED, GOLD, GREEN)

COLS, ROWS = 4, 4
GAP = 8
CW = (W - 16 - (COLS - 1) * GAP) // COLS                 # 110
CH = (H - HUD_H - 12 - (ROWS - 1) * GAP) // ROWS          # 63
PICS = (("sun", C(245, 184, 61)), ("flame", C(255, 140, 60)), ("drop", C(88, 166, 255)),
        ("snow", C(200, 225, 255)), ("bulb", C(250, 220, 120)), ("bolt", C(211, 169, 74)),
        ("home", C(63, 185, 80)), ("alarm", C(248, 81, 73)))
BACK = C(52, 44, 30)


class Game(Base):
    KEY = "memory"
    TITLE = "Campsite Memory"
    LOWER_BETTER = True

    def new(self):
        deck = list(range(8)) * 2
        for i in range(len(deck) - 1, 0, -1):          # shuffle
            j = random.getrandbits(8) % (i + 1)
            deck[i], deck[j] = deck[j], deck[i]
        self.deck = deck
        self.up = [False] * 16                          # face up for good
        self.open = []                                  # turned this move
        self.moves, self.done = 0, False
        self.t0 = time.ticks_ms()
        self.hide_at = None

    def card_at(self, x, y):
        for i in range(16):
            cx, cy = self.pos(i)
            if cx <= x < cx + CW and cy <= y < cy + CH:
                return i
        return None

    @staticmethod
    def pos(i):
        return 8 + (i % COLS) * (CW + GAP), HUD_H + 6 + (i // COLS) * (CH + GAP)

    def draw(self):
        fb, f = self.fb, self.f
        fb.fill(BG)
        secs = time.ticks_diff(self.end if self.done else time.ticks_ms(), self.t0) // 1000
        self.hud("Moves %d · %d:%02d" % (self.moves, secs // 60, secs % 60),
                 ("Best %d" % self.best) if self.best else "")
        for i in range(16):
            x, y = self.pos(i)
            shown = self.up[i] or i in self.open
            if shown:
                back = PANEL2 if not self.up[i] else C(38, 50, 40)
                gfx.rrect(fb, x, y, CW, CH, 10, GOLD if i in self.open else C(70, 120, 80))
                gfx.rrect(fb, x + 2, y + 2, CW - 4, CH - 4, 9, back)
                name, col = PICS[self.deck[i]]
                icons.draw(self.aa, name, x + CW // 2 - 17, y + CH // 2 - 17, col, 34)
            else:
                gfx.rrect(fb, x, y, CW, CH, 10, BACK)
                gfx.rrect(fb, x + 4, y + 4, CW - 8, CH - 8, 8, C(70, 58, 36))
                f.mdb.text(fb, "C", x + CW // 2, y + CH // 2 - 10, GOLD, C(70, 58, 36), 1)
        if self.done:
            self.panel("All pairs found", (("%d moves in %d:%02d" % (self.moves, secs // 60, secs % 60), TXT),
                                           ("Best %d moves" % self.best, GOLD),
                                           ("Tap to play again · X to leave", TXT)))
        self.d.show()

    async def run(self, stop):
        self.aa = AA(self.d.buf, W, H, self.fb)
        if self.begin():
            self.new()
        dirty, sec = True, -1
        while not stop():
            now = time.ticks_ms()
            ev = self.tp.poll()
            if ev and ev[0] == "up":
                x, y = ev[3], ev[4]
                if self.is_exit(x, y):
                    return
                if self.done:
                    self.new()
                elif self.hide_at is None:
                    i = self.card_at(x, y)
                    if i is not None and not self.up[i] and i not in self.open:
                        self.open.append(i)
                        self.sfx("tap")
                        if len(self.open) == 2:
                            self.moves += 1
                            a, b = self.open
                            if self.deck[a] == self.deck[b]:
                                self.up[a] = self.up[b] = True
                                self.open = []
                                self.sfx("ok")
                                if all(self.up):
                                    self.done, self.end = True, now
                                    self.record(self.moves)
                                    self.sfx("chime")
                            else:
                                self.hide_at = time.ticks_add(now, 900)
                dirty = True
            if self.hide_at is not None and time.ticks_diff(now, self.hide_at) >= 0:
                self.open, self.hide_at = [], None
                dirty = True
            s = time.ticks_diff(now, self.t0) // 1000
            if s != sec and not self.done:                    # the clock in the top bar
                sec, dirty = s, True
            if dirty:
                self.draw()
                dirty = False
            await asyncio.sleep_ms(15)
