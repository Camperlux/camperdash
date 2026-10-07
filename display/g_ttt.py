# Noughts & Crosses: against the display, or two players taking turns.
#
# You are X. Against the display it plays O - Easy makes mistakes, Hard never
# loses. Whoever did not start the last game starts the next. The best score
# kept is the number of games won against the display on Hard.

import asyncio
import random

import gfx
from aa import AA
from arcade import (Base, W, H, HUD_H, C, BG, PANEL, PANEL2, TXT, MUTED, GOLD, RED, BLUE, BLACK, hi_get, hi_set)

S, G = 84, 6
SIZE = 3 * S + 2 * G                                 # 264
BX, BY = (W - SIZE) // 2, HUD_H + (H - HUD_H - SIZE) // 2
LINES = ((0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6))
BTN = {"mode": (6, HUD_H + 14, 96, 50), "level": (6, HUD_H + 74, 96, 50), "new": (W - 102, H - 64, 96, 50)}
XC, OC = RED, BLUE


def winner(b):
    for a, c, d in LINES:
        if b[a] and b[a] == b[c] == b[d]:
            return b[a], (a, c, d)
    return (None, None) if None in b else ("draw", None)


def best_move(b, me):
    """Perfect play: the move with the best outcome for `me`, by minimax.
    Positions already worked out are remembered, which on the display turns
    tens of thousands of games into a few thousand positions."""
    them = "X" if me == "O" else "O"
    memo = {}

    def score(b, turn):
        key = (tuple(b), turn)
        v = memo.get(key)
        if v is not None:
            return v
        w, _ = winner(b)
        moves = 9 - b.count(None)
        if w == me:
            v = 10 - moves                           # a quicker win is better
        elif w == them:
            v = moves - 10                           # a later loss is less bad
        elif w == "draw":
            v = 0
        else:
            vals = []
            for i in range(9):
                if b[i] is None:
                    b[i] = turn
                    vals.append(score(b, them if turn == me else me))
                    b[i] = None
            v = max(vals) if turn == me else min(vals)
        memo[key] = v
        return v

    best, pick = -99, None
    for i in range(9):
        if b[i] is None:
            b[i] = me
            v = score(b, them)
            b[i] = None
            if v > best:
                best, pick = v, i
    return pick


class Game(Base):
    KEY = "ttt"
    TITLE = "Noughts & Crosses"

    def new(self):
        self.b = [None] * 9
        self.turn = self.first
        self.result = None
        self.line = None
        self.think_at = None

    def play(self, i):
        self.b[i] = self.turn
        self.sfx("tap")
        w, line = winner(self.b)
        if w:
            self.result, self.line = w, line
            if w == "draw":
                self.tally[2] += 1
            else:
                self.tally[0 if w == "X" else 1] += 1
                if w == "X" and self.solo and self.hard:
                    self.wins += 1
                    hi_set(self.KEY, self.wins)
            self.sfx("chime" if w == "X" or not self.solo else "bad" if w == "O" else "ok")
            self.first = "O" if self.first == "X" else "X"      # the other starts next
        else:
            self.turn = "O" if self.turn == "X" else "X"

    def computer(self):
        free = [i for i in range(9) if self.b[i] is None]
        if not self.hard and random.getrandbits(2):             # Easy: mostly random
            return free[random.getrandbits(4) % len(free)]
        # The opening from the book: the centre, or a corner if it is taken.
        # That is perfect play, and searching it took 15 s on the display.
        if len(free) >= 8:
            return 4 if self.b[4] is None else 0
        return best_move(self.b, "O")

    def draw(self):
        fb, f, a = self.fb, self.f, self.aa
        fb.fill(BG)
        self.hud("X %d · O %d · draws %d" % tuple(self.tally),
                 ("%d wins" % self.wins) if self.wins else "")
        self.button(BTN["mode"], "1 player" if self.solo else "2 players", font=f.sm)
        if self.solo:
            self.button(BTN["level"], "Hard" if self.hard else "Easy", lit=self.hard, font=f.sm)
        self.button(BTN["new"], "New game", font=f.sm)
        gfx.rrect(fb, BX - 6, BY - 6, SIZE + 12, SIZE + 12, 14, PANEL)
        for i in range(9):
            x, y = BX + (i % 3) * (S + G), BY + (i // 3) * (S + G)
            lit = self.line and i in self.line
            back = C(60, 52, 30) if lit else PANEL2
            gfx.rrect(fb, x, y, S, S, 10, back)
            v = self.b[i]
            cx, cy = x + S // 2, y + S // 2
            if v == "X":
                a.line(cx - 22, cy - 22, cx + 22, cy + 22, 9, XC)
                a.line(cx + 22, cy - 22, cx - 22, cy + 22, 9, XC)
            elif v == "O":
                a.ring(cx, cy, 18, 27, OC)
        # whose turn, or how it ended, in the column beside the board
        again = ["Tap the", "board to", "play again"]
        if self.result == "draw":
            msg, sub = ["A draw"], again
        elif self.result:
            if self.solo:
                msg = ["You win!"] if self.result == "X" else ["The", "display", "wins"]
            else:
                msg = ["%s wins!" % self.result]
            sub = again
        elif self.solo:
            msg, sub = (["Your turn"] if self.turn == "X" else ["Thinking\u2026"]), ["You are X"]
        else:
            msg, sub = ["%s to play" % self.turn], ["Take turns"]
        y = HUD_H + 16
        for ln in msg:
            f.md.text(fb, ln, W - 54, y, GOLD, BG, 1)
            y += 24
        y += 10
        for ln in sub:
            f.sm.text(fb, ln, W - 54, y, MUTED, BG, 1)
            y += 20
        self.d.show()

    async def run(self, stop):
        self.aa = AA(self.d.buf, W, H, self.fb)
        if self.begin():
            self.solo, self.hard = True, False
            self.first = "X"
            self.tally = [0, 0, 0]
            self.wins = hi_get(self.KEY, 0)
            self.new()
        dirty = True
        while not stop():
            ev = self.tp.poll()
            if ev and ev[0] == "up":
                x, y = ev[3], ev[4]
                if self.is_exit(x, y):
                    return
                b = self.hit(BTN, x, y)
                if b == "mode":
                    self.solo = not self.solo
                    self.tally = [0, 0, 0]
                    self.new()
                elif b == "level" and self.solo:
                    self.hard = not self.hard
                    self.new()
                elif b == "new":
                    self.new()
                elif self.result:
                    if BX <= x < BX + SIZE and BY <= y < BY + SIZE:
                        self.new()
                elif not (self.solo and self.turn == "O"):
                    for i in range(9):
                        cx, cy = BX + (i % 3) * (S + G), BY + (i // 3) * (S + G)
                        if cx <= x < cx + S and cy <= y < cy + S and self.b[i] is None:
                            self.play(i)
                            break
                dirty = True
            if self.solo and self.turn == "O" and not self.result:
                if self.think_at is None:
                    self.think_at = 0
                    self.draw()                                  # show "Thinking" first
                    await asyncio.sleep_ms(350)
                else:
                    self.play(self.computer())
                    self.think_at = None
                    dirty = True
            if dirty:
                self.draw()
                dirty = False
            await asyncio.sleep_ms(15)
