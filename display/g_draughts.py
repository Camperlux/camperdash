# Draughts (checkers), English rules: two players on the one display, or you
# (red, at the bottom) against it.
#
# Pieces move diagonally forward on the dark squares; a king moves both ways.
# Taking is compulsory - if you can jump, you must - and a piece that has
# jumped carries on jumping while it can. Reaching the far side crowns a piece,
# and ends the move. Tap a piece, then where it goes (one hop at a time when
# it jumps). The display looks three moves ahead.

import asyncio

import gfx
from arcade import (Base, shuffle, W, H, HUD_H, C, BG, TXT, MUTED, GOLD, RED)

SQ = 36
BX, BY = 4, HUD_H
LIGHT, DARK = C(222, 200, 160), C(110, 80, 55)
PICK = C(120, 170, 90)
SIDE = {1: RED, 2: C(245, 240, 225)}         # 1 red (moves up), 2 white (moves down)


def start():
    b = [0] * 64
    for sq in range(64):
        r, c = divmod(sq, 8)
        if (r + c) % 2 == 1:
            if r < 3:
                b[sq] = 2
            elif r > 4:
                b[sq] = 1
    return b


def owner(p):
    return 0 if p == 0 else (1 if p in (1, 3) else 2)


def dirs(p):
    if p >= 3:
        return ((1, 1), (1, -1), (-1, 1), (-1, -1))
    return ((-1, 1), (-1, -1)) if p == 1 else ((1, 1), (1, -1))


def jumps_from(b, sq):
    """Single jumps from sq: [(to, over)]."""
    p = b[sq]
    r, c = divmod(sq, 8)
    out = []
    for dr, dc in dirs(p):
        mr, mc, tr, tc = r + dr, c + dc, r + 2 * dr, c + 2 * dc
        if 0 <= tr < 8 and 0 <= tc < 8 and b[tr * 8 + tc] == 0:
            o = owner(b[mr * 8 + mc])
            if o and o != owner(p):
                out.append((tr * 8 + tc, mr * 8 + mc))
    return out


def steps_from(b, sq):
    p = b[sq]
    r, c = divmod(sq, 8)
    return [(r + dr) * 8 + c + dc for dr, dc in dirs(p)
            if 0 <= r + dr < 8 and 0 <= c + dc < 8 and b[(r + dr) * 8 + c + dc] == 0]


def crowned(p, sq):
    return (p == 1 and sq < 8) or (p == 2 and sq >= 56)


def sequences(b, sq):
    """Every full capture run from sq: lists of squares landed on."""
    out = []

    def go(board, at, path):
        more = False
        p = board[at]
        if path and crowned(p, at):
            out.append(path)          # crowning ends the move
            return
        for to, over in jumps_from(board, at):
            more = True
            nb = board[:]
            nb[to], nb[at], nb[over] = p, 0, 0
            go(nb, to, path + [to])
        if not more and path:
            out.append(path)
    go(b, sq, [])
    return out


def moves(b, who):
    """Every legal move for who: (from, [landing squares]). Jumps if any."""
    caps, plain = [], []
    for sq in range(64):
        if owner(b[sq]) != who:
            continue
        for seq in sequences(b, sq):
            caps.append((sq, seq))
        if not caps:
            for t in steps_from(b, sq):
                plain.append((sq, [t]))
    return caps if caps else plain


def apply(b, mv):
    b = b[:]
    f, path = mv
    p = b[f]
    at = f
    for t in path:
        if abs(t - at) in (14, 18):
            b[(t + at) // 2] = 0
        b[t], b[at] = p, 0
        at = t
    if crowned(p, at):
        b[at] = p + 2
    return b


def score(b, who):
    s = 0
    for sq in range(64):
        p = b[sq]
        if p:
            r = sq // 8
            v = 30 if p >= 3 else 10 + ((7 - r) if p == 1 else r) * 0.5
            s += v if owner(p) == who else -v
    return s


def search(b, who, depth, alpha, beta):
    ms = moves(b, who)
    if not ms:
        return -1000 - depth
    if depth == 0:
        return score(b, who)
    for mv in ms:
        v = -search(apply(b, mv), 3 - who, depth - 1, -beta, -alpha)
        if v > alpha:
            alpha = v
            if alpha >= beta:
                break
    return alpha


def best(b, who, depth=3):
    ms = moves(b, who)
    shuffle(ms)
    bm, bv = ms[0], -10 ** 9
    for mv in ms:
        v = -search(apply(b, mv), 3 - who, depth - 1, -10 ** 9, -bv)
        if v > bv:
            bv, bm = v, mv
    return bm


class Game(Base):
    KEY = "draughts"
    TITLE = "Draughts"

    def new(self):
        self.b = start()
        self.turn = 1
        self.pick = None
        self.partial = []          # hops made so far in a capture run
        self.winner = None

    def options(self):
        """Legal moves for the side to play: whole capture runs, which the
        hops already made (self.partial) narrow down."""
        return moves(self.b, self.turn)

    def shown(self):
        """The board as drawn: with the hops made so far in a capture run."""
        if not self.partial:
            return self.b
        b = self.b[:]
        at = self.pick_from
        for t in self.partial:
            b[(t + at) // 2] = 0
            b[t], b[at] = b[at], 0
            at = t
        return b

    def targets(self):
        if self.pick is None:
            return []
        n = len(self.partial)
        return sorted({m[1][n] for m in self.options() if m[0] == self.pick_from and m[1][:n] == self.partial
                       and len(m[1]) > n})

    def finish(self, mv):
        self.b = apply(self.b, mv)
        self.turn = 3 - self.turn
        self.pick, self.partial = None, []
        if not moves(self.b, self.turn):
            self.winner = 3 - self.turn
            if self.solo and self.winner == 1:
                self.wins += 1
                self.record(self.wins)
            self.sfx("chime" if not self.solo or self.winner == 1 else "bad")

    def tap(self, sq):
        if self.pick is not None and sq in self.targets():
            self.partial = self.partial + [sq]
            done = [m for m in self.options() if m[0] == self.pick_from and m[1] == self.partial]
            self.sfx("tap")
            if done:
                self.finish(done[0])
            else:
                self.pick = sq            # carry on jumping from here
            return
        if self.partial:
            return                         # mid-capture: must carry on
        if owner(self.b[sq]) == self.turn and any(m[0] == sq for m in self.options()):
            self.pick = self.pick_from = sq
        else:
            self.pick = None

    def draw(self, thinking=False):
        fb, f = self.fb, self.f
        name = {1: "Red", 2: "White"}
        self.hud(("%s to move" % name[self.turn]) if self.winner is None else "",
                 "1 player" if self.solo else "2 players")
        fb.fill_rect(0, HUD_H, W, H - HUD_H, BG)
        tg = self.targets()
        must = {m[0] for m in self.options()} if self.winner is None and not self.partial else set()
        board = self.shown()
        for sq in range(64):
            r, c = divmod(sq, 8)
            x, y = BX + c * SQ, BY + r * SQ
            dark = (r + c) % 2 == 1
            fb.fill_rect(x, y, SQ, SQ, PICK if sq == self.pick else DARK if dark else LIGHT)
            p = board[sq]
            if p:
                col = SIDE[owner(p)]
                fb.ellipse(x + SQ // 2, y + SQ // 2 + 1, 14, 14, C(20, 18, 22), True)
                fb.ellipse(x + SQ // 2, y + SQ // 2, 13, 13, col, True)
                fb.ellipse(x + SQ // 2, y + SQ // 2, 9, 9, gfx.blend(col, C(0, 0, 0), 0.18))
                if p >= 3:
                    f.sm.text(fb, "K", x + SQ // 2, y + 9, GOLD, col, 1)
                if (self.turn == 1 or not self.solo) and sq in must and len(must) < 12 and \
                        any(len(m[1]) and abs(m[1][0] - m[0]) in (14, 18) for m in self.options()):
                    fb.ellipse(x + SQ // 2, y + SQ // 2, 16, 16, GOLD)       # a capture is due
            if sq in tg:
                fb.ellipse(x + SQ // 2, y + SQ // 2, 5, 5, C(40, 90, 40), True)
        sx = BX + 8 * SQ + 12
        if self.winner is not None:
            msg = ("You win!" if self.winner == 1 else "The display wins") if self.solo \
                else ("%s wins!" % name[self.winner])
            f.md.text(fb, msg, sx, HUD_H + 10, GOLD, BG)
        elif thinking:
            f.md.text(fb, "Thinking...", sx, HUD_H + 10, MUTED, BG)
        else:
            fb.ellipse(sx + 14, HUD_H + 22, 12, 12, SIDE[self.turn], True)
            f.sm.text(fb, "Jumps must be taken", sx, HUD_H + 44, MUTED, BG)
        if self.solo:
            f.sm.text(fb, "Your wins %d" % self.wins, sx, HUD_H + 70, GOLD, BG)
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
                elif (self.winner is None and BX <= x < BX + 8 * SQ and BY <= y < BY + 8 * SQ
                      and not (self.solo and self.turn == 2)):
                    self.tap(((y - BY) // SQ) * 8 + (x - BX) // SQ)
                dirty = True
            if self.solo and self.turn == 2 and self.winner is None:
                self.draw(thinking=True)
                await asyncio.sleep_ms(30)
                self.finish(best(self.b, 2))
                self.sfx("tap")
                dirty = True
            if dirty:
                self.draw()
                dirty = False
            await asyncio.sleep_ms(20)
