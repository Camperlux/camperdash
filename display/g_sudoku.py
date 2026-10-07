# Sudoku: fill the grid so every row, column and 3 x 3 box holds 1 to 9.
#
# Tap a square, then a number; Erase clears it. A number that breaks the rule
# shows red. Twelve puzzles - four each easy, medium and hard, every one with
# a single solution (made and checked on a PC) - each shuffled before it is
# shown (the digits relabelled, rows and columns swapped within their bands,
# the bands swapped, the grid turned over), so the same one never looks the
# same twice. New picks the next level. Best: the quickest solve.

import asyncio
import random
import time

from arcade import (Base, W, H, HUD_H, C, BG, PANEL, TXT, MUTED, GOLD, RED, BLUE)

PUZZLES = (
    ("easy", "800500706005673080600000005092840310067350490408917502700209050050000600001000003",
     "819524736245673981673198245592846317167352498438917562786239154354781629921465873"),
    ("easy", "700043680408950002629001435001090560800310000500600090907508300050000820200060050",
     "715243689438956712629871435371492568896315274542687193967528341154739826283164957"),
    ("easy", "403900000179805430006032001007600008508209040931704500600047209394000050000000010",
     "423971865179865432856432971247653198568219743931784526615347289394128657782596314"),
    ("easy", "607940038034005109900306402400720001261009300098000005006000020072004803109000050",
     "627941538834275169915386472453728691261459387798163245386517924572694813149832756"),
    ("medium", "060200900100000008079000023040300081205100400000940300014035070706008005050009000",
     "568273914123594768479861523947352681235186497681947352814635279796428135352719846"),
    ("medium", "000000003040020815020030000700204001002001008530006240205010004318040090000007002",
     "157468923643729815829135476786294351492351768531876249275913684318642597964587132"),
    ("medium", "604007030000600050001000040150896000070030006860004021005470000310280004207000000",
     "694517238728643159531928647152896473479132586863754921985471362316285794247369815"),
    ("medium", "020306010000100907000000360090000050060010700350700609002000090800924070047600230",
     "429376815683145927175298364798463152264519783351782649512837496836924571947651238"),
    ("hard", "000003080000600000008200109070300600306000000000500020081460005600005040509007001",
     "152793486493681752768254139875312694326948517914576823281469375637125948549837261"),
    ("hard", "700009060803007590005800307000000800000000030052001040000600003007102900000040080",
     "721359468863417592495826317146573829978264135352981746514698273687132954239745681"),
    ("hard", "000940327007006000050000108100080900000090050790400000806304000009000005030000006",
     "681945327327816549954237168163582974248791653795463812876354291419628735532179486"),
    ("hard", "083601402000040007020000000600080000090500800000206900008090240007008000002004500",
     "783651492156942387429873156631489725294517863875236914368795241547128639912364578"),
)
LEVELS = ("easy", "medium", "hard")
LEVEL_NAME = ("Easy", "Medium", "Hard")     # MicroPython's str has no capitalize()
CELL = 31
GX, GY = 6, HUD_H + 4
PAD_X = GX + 9 * CELL + 14
KEY_W, KEY_H = 54, 46
SEL = C(60, 70, 110)
SAME = C(45, 45, 70)


def _mix(n):
    """0..n-1 in a random order."""
    out = list(range(n))
    for i in range(n - 1, 0, -1):
        j = random.randrange(i + 1)
        out[i], out[j] = out[j], out[i]
    return out


def _lines():
    """A row (or column) order that keeps every band of three together."""
    out = []
    for b in _mix(3):
        out += [b * 3 + k for k in _mix(3)]
    return out


def shuffled(p, s):
    """The same puzzle in disguise: each change keeps a valid sudoku valid,
    and its one solution one."""
    digits = [d + 1 for d in _mix(9)]
    rows, cols = _lines(), _lines()
    flip = random.randrange(2)

    def remap(src):
        out = []
        for r in range(9):
            for c in range(9):
                rr, cc = (cols[c], rows[r]) if flip else (rows[r], cols[c])
                v = int(src[rr * 9 + cc])
                out.append(digits[v - 1] if v else 0)
        return out
    return remap(p), remap(s)


class Game(Base):
    KEY = "sudoku"
    TITLE = "Sudoku"
    LOWER_BETTER = True

    def new(self, level=None):
        if level is not None:
            self.level = level
        choices = [k for k in PUZZLES if k[0] == LEVELS[self.level]]
        _, p, s = choices[random.randrange(len(choices))]
        self.given, self.sol = shuffled(p, s)
        self.g = self.given[:]
        self.sel = None
        self.t0 = None
        self.won = False
        self.secs = 0

    def clash(self, i):
        v = self.g[i]
        if not v:
            return False
        r, c = divmod(i, 9)
        for k in range(9):
            if (k != c and self.g[r * 9 + k] == v) or (k != r and self.g[k * 9 + c] == v):
                return True
        br, bc = r // 3 * 3, c // 3 * 3
        for rr in range(br, br + 3):
            for cc in range(bc, bc + 3):
                if (rr, cc) != (r, c) and self.g[rr * 9 + cc] == v:
                    return True
        return False

    def keys(self):
        out = {}
        for d in range(1, 10):
            out[d] = (PAD_X + ((d - 1) % 3) * (KEY_W + 4), GY + ((d - 1) // 3) * (KEY_H + 4), KEY_W, KEY_H)
        out["erase"] = (PAD_X, GY + 3 * (KEY_H + 4), 3 * KEY_W + 8, 40)
        out["new"] = (PAD_X, GY + 3 * (KEY_H + 4) + 46, 3 * KEY_W + 8, 40)
        return out

    def draw(self):
        fb, f = self.fb, self.f
        if self.won:
            secs = self.secs
        else:
            secs = time.ticks_diff(time.ticks_ms(), self.t0) // 1000 if self.t0 else 0
        self.hud("%s  %d:%02d" % (LEVEL_NAME[self.level], secs // 60, secs % 60),
                 ("Best %d:%02d" % (self.best // 60, self.best % 60)) if self.best else "")
        fb.fill_rect(0, HUD_H, W, H - HUD_H, BG)
        selv = self.g[self.sel] if self.sel is not None else 0
        for i in range(81):
            r, c = divmod(i, 9)
            x, y = GX + c * CELL, GY + r * CELL
            back = SEL if i == self.sel else SAME if selv and self.g[i] == selv else PANEL
            fb.fill_rect(x, y, CELL - 1, CELL - 1, back)
            v = self.g[i]
            if v:
                col = TXT if self.given[i] else (RED if self.clash(i) else BLUE)
                f.md.text(fb, str(v), x + CELL // 2, y + 4, col, back, 1)
        for k in (0, 3, 6, 9):                     # the boxes
            fb.fill_rect(GX + k * CELL - 1, GY - 1, 2, 9 * CELL + 1, GOLD)
            fb.fill_rect(GX - 1, GY + k * CELL - 1, 9 * CELL + 1, 2, GOLD)
        for k, rect in self.keys().items():
            if isinstance(k, int):
                self.button(rect, str(k), font=f.md)
            else:
                lab = "Erase" if k == "erase" else "New: " + LEVELS[(self.level + 1) % 3]
                self.button(rect, lab, font=f.sm)
        if self.won:
            best = " - a new best!" if self.secs == self.best else ""
            self.panel("Solved!", (("%d:%02d%s" % (self.secs // 60, self.secs % 60, best), TXT),
                                   ("Tap New for another", MUTED)))
        self.d.show()

    async def run(self, stop):
        if self.begin():
            self.level = 0
            self.new()
        dirty = True
        shown = -1
        while not stop():
            ev = self.tp.poll()
            if ev and ev[0] == "up":
                x, y = ev[3], ev[4]
                if self.is_exit(x, y):
                    return
                k = self.hit(self.keys(), x, y)
                if k == "new":
                    self.new((self.level + 1) % 3)
                elif self.won:
                    pass
                elif GX <= x < GX + 9 * CELL and GY <= y < GY + 9 * CELL:
                    self.sel = ((y - GY) // CELL) * 9 + (x - GX) // CELL
                elif k is not None and self.sel is not None and not self.given[self.sel]:
                    if self.t0 is None:
                        self.t0 = time.ticks_ms()
                    self.g[self.sel] = 0 if k == "erase" else k
                    self.sfx("tap")
                    if self.g == self.sol:
                        self.won = True
                        self.secs = time.ticks_diff(time.ticks_ms(), self.t0) // 1000
                        self.record(max(1, self.secs))
                        self.sfx("chime")
                dirty = True
            if self.t0 and not self.won:
                s = time.ticks_diff(time.ticks_ms(), self.t0) // 1000
                if s != shown:
                    shown, dirty = s, True
            if dirty:
                self.draw()
                dirty = False
            await asyncio.sleep_ms(20)
