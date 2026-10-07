# Chess: two players on the one display, or you (white) against it.
#
# Tap a piece to pick it up - its moves show as dots - then tap where it goes.
# The full rules: castling, en passant, promotion (to a queen), check,
# checkmate and stalemate. The display looks two moves ahead (its own and your
# answer), weighing material and a little position: a fair game for a
# beginner, not a grandmaster.

import asyncio

import framebuf

import gfx
from arcade import (Base, shuffle, W, H, HUD_H, C, BG, PANEL2, TXT, MUTED, GOLD, RED, GREEN)

SQ = 36
BX, BY = 4, HUD_H
LIGHT, DARK = C(222, 200, 160), C(150, 110, 75)
PICK, LAST = C(120, 170, 90), C(200, 170, 70)
KEY = C(255, 0, 255)
VAL = {"P": 100, "N": 320, "B": 330, "R": 500, "Q": 900, "K": 0}
N_OFF = (-17, -15, -10, -6, 6, 10, 15, 17)
# 12 x 12 masks, drawn double size with an outline worked out round them
SHAPES = {
    "P": ("            ", "            ", "     XX     ", "    XXXX    ", "    XXXX    ", "     XX     ",
          "    XXXX    ", "     XX     ", "    XXXX    ", "   XXXXXX   ", "  XXXXXXXX  ", "            "),
    "R": ("            ", "  XX XX XX  ", "  XXXXXXXX  ", "   XXXXXX   ", "   XXXXXX   ", "   XXXXXX   ",
          "   XXXXXX   ", "   XXXXXX   ", "  XXXXXXXX  ", " XXXXXXXXXX ", " XXXXXXXXXX ", "            "),
    "N": ("            ", "    X X     ", "   XXXXX    ", "  XXXXXXX   ", " XXX XXXXX  ", " XX  XXXXX  ",
          "     XXXXX  ", "    XXXXXX  ", "   XXXXXXX  ", "  XXXXXXXX  ", " XXXXXXXXXX ", "            "),
    "B": ("            ", "     XX     ", "     XX     ", "    XXXX    ", "   XXX XX   ", "   XX XXX   ",
          "   XXXXXX   ", "    XXXX    ", "     XX     ", "   XXXXXX   ", "  XXXXXXXX  ", "            "),
    "Q": ("            ", " X   XX   X ", " XX  XX  XX ", " XXX XX XXX ", " XXXXXXXXXX ", "  XXXXXXXX  ",
          "  XXXXXXXX  ", "   XXXXXX   ", "   XXXXXX   ", "  XXXXXXXX  ", " XXXXXXXXXX ", "            "),
    "K": ("     XX     ", "    XXXX    ", "     XX     ", "  XX XX XX  ", " XXXXXXXXXX ", " XXXXXXXXXX ",
          "  XXXXXXXX  ", "   XXXXXX   ", "   XXXXXX   ", "  XXXXXXXX  ", " XXXXXXXXXX ", "            "),
}


# ---- the rules ---------------------------------------------------------------------
# A position: (board, white to move, castling rights "KQkq", en passant square
# or -1). The board is 64 squares, a8 first; white pieces upper case.

def start():
    b = list("rnbqkbnr" + "p" * 8 + "." * 32 + "P" * 8 + "RNBQKBNR")
    return (b, True, "KQkq", -1)


def _white(p):
    return "A" <= p <= "Z"


def attacked(b, sq, by_white):
    """Is sq attacked by the side by_white?"""
    r, c = divmod(sq, 8)
    # pawns
    pr = r + 1 if by_white else r - 1
    pawn = "P" if by_white else "p"
    if 0 <= pr < 8:
        for dc in (-1, 1):
            if 0 <= c + dc < 8 and b[pr * 8 + c + dc] == pawn:
                return True
    knight = "N" if by_white else "n"
    for dr, dc in ((1, 2), (2, 1), (-1, 2), (-2, 1), (1, -2), (2, -1), (-1, -2), (-2, -1)):
        rr, cc = r + dr, c + dc
        if 0 <= rr < 8 and 0 <= cc < 8 and b[rr * 8 + cc] == knight:
            return True
    king = "K" if by_white else "k"
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            rr, cc = r + dr, c + dc
            if (dr or dc) and 0 <= rr < 8 and 0 <= cc < 8 and b[rr * 8 + cc] == king:
                return True
    rq = ("R", "Q") if by_white else ("r", "q")
    bq = ("B", "Q") if by_white else ("b", "q")
    for dr, dc, who in ((1, 0, rq), (-1, 0, rq), (0, 1, rq), (0, -1, rq),
                        (1, 1, bq), (1, -1, bq), (-1, 1, bq), (-1, -1, bq)):
        rr, cc = r + dr, c + dc
        while 0 <= rr < 8 and 0 <= cc < 8:
            p = b[rr * 8 + cc]
            if p != ".":
                if p in who:
                    return True
                break
            rr += dr
            cc += dc
    return False


def pseudo(pos):
    """Every move (from, to) the side to move could make, before checking
    whether it leaves its own king in check."""
    b, wt, castle, ep = pos
    out = []
    for sq in range(64):
        p = b[sq]
        if p == "." or _white(p) != wt:
            continue
        r, c = divmod(sq, 8)
        k = p.upper()
        if k == "P":
            d = -1 if wt else 1
            rr = r + d
            if 0 <= rr < 8 and b[rr * 8 + c] == ".":
                out.append((sq, rr * 8 + c))
                if (r == 6 and wt or r == 1 and not wt) and b[(r + 2 * d) * 8 + c] == ".":
                    out.append((sq, (r + 2 * d) * 8 + c))
            for dc in (-1, 1):
                cc = c + dc
                if 0 <= rr < 8 and 0 <= cc < 8:
                    t = rr * 8 + cc
                    if (b[t] != "." and _white(b[t]) != wt) or t == ep:
                        out.append((sq, t))
        elif k == "N" or k == "K":
            steps = (((1, 2), (2, 1), (-1, 2), (-2, 1), (1, -2), (2, -1), (-1, -2), (-2, -1)) if k == "N"
                     else ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)))
            for dr, dc in steps:
                rr, cc = r + dr, c + dc
                if 0 <= rr < 8 and 0 <= cc < 8:
                    t = rr * 8 + cc
                    if b[t] == "." or _white(b[t]) != wt:
                        out.append((sq, t))
            if k == "K":
                # castling: rights kept, squares empty, and not through check
                home = 60 if wt else 4
                if sq == home and not attacked(b, sq, not wt):
                    ks, qs = ("K", "Q") if wt else ("k", "q")
                    if ks in castle and b[sq + 1] == "." and b[sq + 2] == "." \
                            and not attacked(b, sq + 1, not wt):
                        out.append((sq, sq + 2))
                    if qs in castle and b[sq - 1] == "." and b[sq - 2] == "." and b[sq - 3] == "." \
                            and not attacked(b, sq - 1, not wt):
                        out.append((sq, sq - 2))
        else:
            dirs = []
            if k in "RQ":
                dirs += [(1, 0), (-1, 0), (0, 1), (0, -1)]
            if k in "BQ":
                dirs += [(1, 1), (1, -1), (-1, 1), (-1, -1)]
            for dr, dc in dirs:
                rr, cc = r + dr, c + dc
                while 0 <= rr < 8 and 0 <= cc < 8:
                    t = rr * 8 + cc
                    if b[t] == ".":
                        out.append((sq, t))
                    else:
                        if _white(b[t]) != wt:
                            out.append((sq, t))
                        break
                    rr += dr
                    cc += dc
    return out


def make(pos, mv):
    """The position after mv."""
    b, wt, castle, ep = pos
    f, t = mv
    b = b[:]
    p = b[f]
    k = p.upper()
    nep = -1
    if k == "P":
        if t == ep:
            b[t + (8 if wt else -8)] = "."            # en passant
        if abs(t - f) == 16:
            nep = (f + t) // 2
        if t < 8 or t >= 56:
            p = "Q" if wt else "q"                    # promotion: a queen
    if k == "K":
        if t - f == 2:
            b[f + 1], b[f + 3] = b[f + 3], "."
        elif f - t == 2:
            b[f - 1], b[f - 4] = b[f - 4], "."
        castle = castle.replace("K" if wt else "k", "").replace("Q" if wt else "q", "")
    for sq, right in ((63, "K"), (56, "Q"), (7, "k"), (0, "q")):
        if f == sq or t == sq:
            castle = castle.replace(right, "")
    b[t], b[f] = p, "."
    return (b, not wt, castle, nep)


def in_check(pos):
    b, wt = pos[0], pos[1]
    k = b.index("K" if wt else "k")
    return attacked(b, k, not wt)


def legal(pos):
    out = []
    wt = pos[1]
    for mv in pseudo(pos):
        n = make(pos, mv)
        k = n[0].index("K" if wt else "k")
        if not attacked(n[0], k, not wt):
            out.append(mv)
    return out


def evaluate(b):
    """From white's side: material, and a nudge for pawns forward and pieces
    toward the middle."""
    s = 0
    for sq in range(64):
        p = b[sq]
        if p == ".":
            continue
        r, c = divmod(sq, 8)
        k = p.upper()
        v = VAL[k]
        if k == "P":
            v += (6 - r) * 6 if _white(p) else (r - 1) * 6
        elif k in "NB":
            v += 12 - 3 * (abs(3.5 - r) + abs(3.5 - c))
        s += v if _white(p) else -v
    return s


def search(pos, depth, alpha, beta):
    """Negamax with alpha-beta, from the side to move."""
    moves = legal(pos)
    if not moves:
        return (-100000 - depth) if in_check(pos) else 0
    if depth == 0:
        e = evaluate(pos[0])
        return e if pos[1] else -e
    b = pos[0]
    moves.sort(key=lambda m: -VAL.get(b[m[1]].upper(), 0) if b[m[1]] != "." else 0)
    for mv in moves:
        v = -search(make(pos, mv), depth - 1, -beta, -alpha)
        if v > alpha:
            alpha = v
            if alpha >= beta:
                break
    return alpha


def best_move(pos, depth=2):
    moves = legal(pos)
    shuffle(moves)                 # not the same game every time
    b = pos[0]
    moves.sort(key=lambda m: -VAL.get(b[m[1]].upper(), 0) if b[m[1]] != "." else 0)
    best, bv = moves[0], -10 ** 9
    for mv in moves:
        v = -search(make(pos, mv), depth - 1, -10 ** 9, -bv)
        if v > bv:
            bv, best = v, mv
    return best


# ---- the game ----------------------------------------------------------------------

class Game(Base):
    KEY = "chess"
    TITLE = "Chess"

    def sprites(self):
        """The twelve pieces, drawn once into small images with a see-through
        key colour."""
        self.spr = {}
        body = {True: C(245, 240, 225), False: C(45, 40, 50)}
        rim = {True: C(30, 28, 34), False: C(235, 225, 205)}
        for k, rows in SHAPES.items():
            for wt in (True, False):
                buf = bytearray(24 * 24 * 2)
                fb = framebuf.FrameBuffer(buf, 24, 24, framebuf.RGB565)
                fb.fill(KEY)
                on = lambda x, y: 0 <= x < 12 and 0 <= y < 12 and rows[y][x] == "X"   # noqa: E731
                for y in range(12):
                    for x in range(12):
                        if not on(x, y) and (on(x - 1, y) or on(x + 1, y) or on(x, y - 1) or on(x, y + 1)):
                            fb.fill_rect(x * 2, y * 2, 2, 2, rim[wt])
                        elif on(x, y):
                            fb.fill_rect(x * 2, y * 2, 2, 2, body[wt])
                self.spr[k if wt else k.lower()] = (fb, buf)

    def new(self):
        self.pos = start()
        self.hist = []
        self.pick = None
        self.dests = []
        self.last = None
        self.result = None

    def status(self):
        moves = legal(self.pos)
        if moves:
            return None
        if in_check(self.pos):
            return ("Black" if self.pos[1] else "White") + " wins - checkmate"
        return "Stalemate - a draw"

    def play(self, mv):
        self.hist.append(self.pos)
        self.pos = make(self.pos, mv)
        self.last = mv
        self.pick, self.dests = None, []
        self.result = self.status()
        if self.result:
            if self.solo and self.result.startswith("White"):
                self.wins += 1
                self.record(self.wins)
            self.sfx("chime" if self.result.startswith("White") or not self.solo else "bad")
        else:
            self.sfx("ok" if in_check(self.pos) else "tap")

    def draw(self, thinking=False):
        fb, f = self.fb, self.f
        b, wt = self.pos[0], self.pos[1]
        self.hud(("White" if wt else "Black") + " to move" if not self.result else "",
                 "1 player" if self.solo else "2 players")
        fb.fill_rect(0, HUD_H, W, H - HUD_H, BG)
        for sq in range(64):
            r, c = divmod(sq, 8)
            x, y = BX + c * SQ, BY + r * SQ
            col = LIGHT if (r + c) % 2 == 0 else DARK
            if self.last and sq in self.last:
                col = gfx.blend(col, LAST, 0.45)
            if sq == self.pick:
                col = PICK
            fb.fill_rect(x, y, SQ, SQ, col)
            p = b[sq]
            if p != ".":
                fb.blit(self.spr[p][0], x + 6, y + 6, KEY)
            if sq in self.dests:
                fb.ellipse(x + SQ // 2, y + SQ // 2, 5, 5, C(40, 90, 40), True)
        # the side: whose turn, check, the result, buttons
        sx = BX + 8 * SQ + 12
        if self.result:
            for i, ln in enumerate(self.result.split(" - ")):
                f.md.text(fb, ln, sx, HUD_H + 10 + i * 26, GOLD, BG)
        elif thinking:
            f.md.text(fb, "Thinking...", sx, HUD_H + 10, MUTED, BG)
        else:
            f.md.text(fb, ("White" if wt else "Black") + " to move", sx, HUD_H + 10, TXT, BG)
            if in_check(self.pos):
                f.md.text(fb, "Check!", sx, HUD_H + 38, RED, BG)
        if self.solo:
            f.sm.text(fb, "Your wins %d" % self.wins, sx, HUD_H + 70, GOLD, BG)
        self.button(self.btn["undo"], "Undo", font=f.md)
        self.button(self.btn["new"], "New game", font=f.md)
        self.button(self.btn["mode"], "2 players" if self.solo else "1 player", font=f.md)
        self.d.show()

    async def run(self, stop):
        self.sprites()
        if self.begin():
            self.solo = True
            self.wins = self.best or 0
            self.new()
        sx = BX + 8 * SQ + 10
        bw = W - sx - 6
        self.btn = {"undo": (sx, 150, bw, 46), "new": (sx, 204, bw, 46), "mode": (sx, 258, bw, 46)}
        dirty = True
        try:
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
                    elif btn == "undo" and self.hist:
                        # alone: back to your own last move
                        for _ in range(2 if self.solo and len(self.hist) >= 2 and self.pos[1] else 1):
                            self.pos = self.hist.pop()
                        self.last, self.pick, self.dests, self.result = None, None, [], None
                    elif (not self.result and BX <= x < BX + 8 * SQ and BY <= y < BY + 8 * SQ
                          and not (self.solo and not self.pos[1])):
                        sq = ((y - BY) // SQ) * 8 + (x - BX) // SQ
                        p = self.pos[0][sq]
                        if self.pick is not None and sq in self.dests:
                            self.play((self.pick, sq))
                        elif p != "." and _white(p) == self.pos[1]:
                            self.pick = sq
                            self.dests = [t for f_, t in legal(self.pos) if f_ == sq]
                        else:
                            self.pick, self.dests = None, []
                    dirty = True
                # the display's move
                if self.solo and not self.pos[1] and not self.result:
                    self.draw(thinking=True)
                    await asyncio.sleep_ms(30)
                    self.play(best_move(self.pos))
                    dirty = True
                if dirty:
                    self.draw()
                    dirty = False
                await asyncio.sleep_ms(20)
        finally:
            self.spr = None
