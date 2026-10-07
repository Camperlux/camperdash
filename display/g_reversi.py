# Reversi (Othello): two players on the one display, or you (dark) against it.
#
# Place a counter so that it traps a line of the other colour between it and
# one of yours - across, down or diagonally - and they all turn over. You must
# turn over at least one; with no such move you pass. When neither side can
# move, most counters wins. The places you can play are marked. The display
# prizes corners and edges, keeps away from the squares next to a corner, and
# looks two moves ahead (its own, and your answer).

import asyncio

from arcade import (Base, shuffle, W, H, HUD_H, C, BG, TXT, MUTED, GOLD)

SQ = 36
BX, BY = 4, HUD_H
FELT, LINE = C(30, 110, 70), C(20, 80, 50)
DARK, PALE = C(30, 28, 34), C(240, 236, 225)
DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))
WEIGHT = (120, -20, 20, 5, 5, 20, -20, 120,
          -20, -40, -5, -5, -5, -5, -40, -20,
          20, -5, 15, 3, 3, 15, -5, 20,
          5, -5, 3, 3, 3, 3, -5, 5,
          5, -5, 3, 3, 3, 3, -5, 5,
          20, -5, 15, 3, 3, 15, -5, 20,
          -20, -40, -5, -5, -5, -5, -40, -20,
          120, -20, 20, 5, 5, 20, -20, 120)


def start():
    b = [0] * 64
    b[27] = b[36] = 2
    b[28] = b[35] = 1
    return b


def flips(b, sq, who):
    if b[sq]:
        return []
    r, c = divmod(sq, 8)
    out = []
    for dr, dc in DIRS:
        line = []
        rr, cc = r + dr, c + dc
        while 0 <= rr < 8 and 0 <= cc < 8 and b[rr * 8 + cc] == 3 - who:
            line.append(rr * 8 + cc)
            rr += dr
            cc += dc
        if line and 0 <= rr < 8 and 0 <= cc < 8 and b[rr * 8 + cc] == who:
            out += line
    return out


def moves(b, who):
    return [sq for sq in range(64) if not b[sq] and flips(b, sq, who)]


def play(b, sq, who):
    b = b[:]
    for t in flips(b, sq, who):
        b[t] = who
    b[sq] = who
    return b


def value(b, who):
    s = 0
    for sq in range(64):
        if b[sq]:
            s += WEIGHT[sq] if b[sq] == who else -WEIGHT[sq]
    return s + 4 * (len(moves(b, who)) - len(moves(b, 3 - who)))


def search(b, who, depth, alpha, beta):
    ms = moves(b, who)
    if depth == 0 or (not ms and not moves(b, 3 - who)):
        return value(b, who)
    if not ms:
        return -search(b, 3 - who, depth - 1, -beta, -alpha)
    for sq in ms:
        v = -search(play(b, sq, who), 3 - who, depth - 1, -beta, -alpha)
        if v > alpha:
            alpha = v
            if alpha >= beta:
                break
    return alpha


def best(b, who, depth=2):
    ms = moves(b, who)
    shuffle(ms)
    bm, bv = ms[0], -10 ** 9
    for sq in ms:
        v = -search(play(b, sq, who), 3 - who, depth - 1, -10 ** 9, -bv)
        if v > bv:
            bv, bm = v, sq
    return bm


class Game(Base):
    KEY = "reversi"
    TITLE = "Reversi"

    def new(self):
        self.b = start()
        self.turn = 1
        self.over = False
        self.note = ""
        self.last = None

    def count(self, who):
        return sum(1 for p in self.b if p == who)

    def after(self):
        """Pass if the side to move cannot; the end if neither can."""
        if moves(self.b, self.turn):
            return
        if moves(self.b, 3 - self.turn):
            self.note = ("Dark" if self.turn == 1 else "Light") + " has no move - passes"
            self.turn = 3 - self.turn
            return
        self.over = True
        d, l = self.count(1), self.count(2)
        if self.solo and d > l:
            self.wins += 1
            self.record(self.wins)
        self.sfx("chime" if not self.solo or d > l else "bad")

    def put(self, sq):
        self.b = play(self.b, sq, self.turn)
        self.last = sq
        self.note = ""
        self.turn = 3 - self.turn
        self.sfx("tap")
        self.after()

    def draw(self, thinking=False):
        fb, f = self.fb, self.f
        d, l = self.count(1), self.count(2)
        self.hud("Dark %d  Light %d" % (d, l), "1 player" if self.solo else "2 players")
        fb.fill_rect(0, HUD_H, W, H - HUD_H, BG)
        fb.fill_rect(BX, BY, 8 * SQ, 8 * SQ, FELT)
        for k in range(9):
            fb.hline(BX, BY + k * SQ - (1 if k == 8 else 0), 8 * SQ, LINE)
            fb.vline(BX + k * SQ - (1 if k == 8 else 0), BY, 8 * SQ, LINE)
        can = moves(self.b, self.turn) if not self.over and not (self.solo and self.turn == 2) else []
        for sq in range(64):
            r, c = divmod(sq, 8)
            cx, cy = BX + c * SQ + SQ // 2, BY + r * SQ + SQ // 2
            p = self.b[sq]
            if p:
                fb.ellipse(cx, cy + 1, 15, 15, C(10, 40, 25), True)
                fb.ellipse(cx, cy, 14, 14, DARK if p == 1 else PALE, True)
                if sq == self.last:
                    fb.ellipse(cx, cy, 3, 3, GOLD, True)
            elif sq in can:
                fb.ellipse(cx, cy, 4, 4, C(90, 170, 120), True)
        sx = BX + 8 * SQ + 12
        if self.over:
            msg = "A draw" if d == l else (
                ("You win!" if d > l else "The display wins") if self.solo else
                ("Dark wins!" if d > l else "Light wins!"))
            f.md.text(fb, msg, sx, HUD_H + 10, GOLD, BG)
            f.sm.text(fb, "%d - %d" % (d, l), sx, HUD_H + 40, TXT, BG)
        elif thinking:
            f.md.text(fb, "Thinking...", sx, HUD_H + 10, MUTED, BG)
        else:
            fb.ellipse(sx + 14, HUD_H + 22, 12, 12, DARK if self.turn == 1 else PALE, True)
            f.sm.text(fb, "to play", sx + 34, HUD_H + 14, TXT, BG)
        if self.note:
            f.sm.text(fb, self.note[:22], sx, HUD_H + 70, MUTED, BG)
        if self.solo:
            f.sm.text(fb, "Your wins %d" % self.wins, sx, HUD_H + 96, GOLD, BG)
        self.button(self.btn["new"], "New game", font=f.md)
        self.button(self.btn["mode"], "2 players" if self.solo else "1 player", font=f.md)
        self.d.show()

    async def run(self, stop):
        if self.begin():
            self.solo = True
            self.wins = self.best or 0
            self.new()
        sx = BX + 8 * SQ + 10
        bw = W - sx - 6
        self.btn = {"new": (sx, 204, bw, 46), "mode": (sx, 258, bw, 46)}
        dirty = True
        while not stop():
            ev = self.tp.poll()
            if ev and ev[0] == "up":
                x, y = ev[3], ev[4]
                if self.is_exit(x, y):
                    return
                btn = self.hit(self.btn, x, y)
                if btn == "new":
                    self.new()
                elif btn == "mode":
                    self.solo = not self.solo
                    self.new()
                elif (not self.over and BX <= x < BX + 8 * SQ and BY <= y < BY + 8 * SQ
                      and not (self.solo and self.turn == 2)):
                    sq = ((y - BY) // SQ) * 8 + (x - BX) // SQ
                    if sq in moves(self.b, self.turn):
                        self.put(sq)
                dirty = True
            if self.solo and self.turn == 2 and not self.over:
                self.draw(thinking=True)
                await asyncio.sleep_ms(400)
                self.put(best(self.b, 2))
                dirty = True
            if dirty:
                self.draw()
                dirty = False
            await asyncio.sleep_ms(20)
