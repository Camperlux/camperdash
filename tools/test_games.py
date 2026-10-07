"""Run the games on a PC, for a first check before the display: the menu
pages and each new game drawn to PNGs, and a stretch of scripted play for
each, to shake out errors. A stand-in for the display's framebuf, touch panel
and clock; no sound.

  python tools/test_games.py [out dir]
"""

import asyncio
import os
import sys
import time

TOOLS = os.path.dirname(os.path.abspath(__file__))
DISPLAY = os.path.join(os.path.dirname(TOOLS), "display")
sys.path.insert(0, TOOLS)
import preview_display as pv                                   # noqa: E402

pv.install_framebuf()
sys.path.insert(0, DISPLAY)

# MicroPython's time extras
time.ticks_ms = lambda: int(time.monotonic() * 1000)
time.ticks_diff = lambda a, b: a - b
time.ticks_add = lambda a, b: a + b
asyncio.sleep_ms = lambda ms: asyncio.sleep(ms / 1000)

# Be MicroPython, as far as the games can tell: its random module has no
# shuffle() or sample(), and its str lacks a handful of methods - all of which
# CPython has, so a game using them passed here and crashed on the display.
import random                                                  # noqa: E402
for _n in ("shuffle", "sample", "choices"):
    if hasattr(random, _n):
        delattr(random, _n)
_MISSING = (".capitalize(", ".title(", ".swapcase(", ".zfill(", ".center(", ".ljust(", ".rjust(",
            ".casefold(", ".expandtabs(", ".translate(", "random.choices")
_bad = []
for _fn in sorted(os.listdir(DISPLAY)):
    if _fn.endswith(".py"):
        for _i, _ln in enumerate(open(os.path.join(DISPLAY, _fn), encoding="utf-8"), 1):
            for _m in _MISSING:
                if _m in _ln.split("#")[0]:
                    _bad.append("%s:%d uses %s" % (_fn, _i, _m.strip(".(")))
if _bad:
    sys.exit("not in MicroPython: " + "; ".join(_bad))
print("MicroPython check: no missing functions used")

import gfx                                                     # noqa: E402
import arcade                                                  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(TOOLS, "..", "preview", "games")
os.makedirs(OUT, exist_ok=True)
os.chdir(OUT)              # the games' best scores land here, not in the repository


class Disp:
    def __init__(self):
        self.buf = bytearray(480 * 320 * 2)
        self.fb = pv.FrameBuffer(self.buf, 480, 320, pv.FrameBuffer.RGB565)
        self.width, self.height = 480, 320
        self.shows = 0

    def show(self):
        self.shows += 1


class ScriptTouch:
    """A touch panel that plays back a script: [(ms from start, [points])]."""
    def __init__(self, script):
        self.script = sorted(script)
        self.t0 = time.ticks_ms()

    def read_all(self):
        now = time.ticks_diff(time.ticks_ms(), self.t0)
        pts = []
        for t, p in self.script:
            if t <= now:
                pts = p
        return list(pts)

    def read(self):
        p = self.read_all()
        return p[0] if p else None


def shot(disp, name):
    pv.to_png(disp.buf, 480, 320, os.path.join(OUT, name + ".png"))


async def play(mod, script, seconds, shots=()):
    disp = Disp()
    fonts = gfx.Fonts(os.path.join(DISPLAY, "fonts"))
    touch = ScriptTouch(script)
    ctx = arcade._Ctx(disp, fonts, touch, None)
    m = __import__(mod)
    g = m.Game(ctx)
    t0 = time.monotonic()
    done = {}

    def stop():
        el = time.monotonic() - t0
        for at, name in shots:
            if el >= at and name not in done:
                done[name] = True
                shot(disp, name)
        return el > seconds

    await g.run(stop)
    shot(disp, mod + "_end")
    print("%-12s ran %.1fs, %d frames, no errors" % (mod, seconds, disp.shows))
    return g


async def main():
    disp = Disp()
    fonts = gfx.Fonts(os.path.join(DISPLAY, "fonts"))
    for page in range(arcade.PAGES):
        arcade._menu(disp.fb, fonts, page)
        shot(disp, "menu_%d" % (page + 1))
    print("menu: %d pages" % arcade.PAGES)
    # Pong, two players: a finger on each half, moving
    s = [(0, [])] + [(400 + k * 60, [(40, 80 + (k * 13) % 200), (440, 250 - (k * 17) % 200)]) for k in range(80)]
    s += [(200, [(150, 200)]), (260, [])]          # choose "2 players"? no: 1 player is at (145,185)
    s = [(0, []), (150, [(330, 180)]), (250, [])] + [(400 + k * 60, [(40, 80 + (k * 13) % 200), (440, 250 - (k * 17) % 200)]) for k in range(80)]
    g = await play("g_pong", s, 5, ((0.2, "pong_choose"), (3, "pong_play")))
    print("   pong score", g.score, "two players:", not g.solo)
    # Breakout: slide, tap to launch, keep sliding under the ball
    s = [(0, []), (300, [(240, 290)]), (400, [])] + [(600 + k * 40, [(40 + (k * 23) % 400, 290)]) for k in range(120)]
    g = await play("g_breakout", s, 5, ((0.3, "breakout_start"), (4, "breakout_play")))
    print("   breakout score", g.score, "balls left", g.lives)
    # Falling blocks: presses on each button
    s = [(0, [])]
    for k, b in enumerate(["left", "left", "turn", "right", "drop", "turn", "down", "drop", "right", "drop"] * 3):
        x, y, w, h = arcade.__dict__.get("BTN", None) or __import__("g_blocks").BTN[b]
        s += [(300 + k * 150, [(x + w // 2, y + h // 2)]), (360 + k * 150, [])]
    g = await play("g_blocks", s, 5, ((2.5, "blocks_play"),))
    print("   blocks score", g.score, "lines", g.lines)
    # Four in a row against the display: tap columns
    s = [(0, [])]
    for k, c in enumerate([3, 3, 2, 4, 1, 5, 0, 6, 3, 2]):
        s += [(300 + k * 900, [(20 + c * 40 + 20, 150)]), (360 + k * 900, [])]
    g = await play("g_four", s, 9, ((4, "four_play"),))
    print("   four winner", g.winner, "draw" if g.full else "")
    # Minesweeper: a first tap, a flag by holding, more taps
    s = [(0, []), (300, [(240, 170)]), (360, []), (800, [(40, 60)]), (1400, []),
         (1600, [(420, 250)]), (1660, []), (1900, [(100, 250)]), (1960, [])]
    g = await play("g_mines", s, 3, ((1.2, "mines_flag"), (2.5, "mines_play")))
    print("   mines over:", g.over)

    def taps(points, start=300, gap=700):
        out = [(0, [])]
        for k, (x, y) in enumerate(points):
            out += [(start + k * gap, [(x, y)]), (start + k * gap + 60, [])]
        return out
    sq = lambda r, c: (4 + c * 36 + 18, 32 + r * 36 + 18)         # noqa: E731 - board squares
    # Chess: e2-e4, then the display answers
    g = await play("g_chess", taps([sq(6, 4), sq(4, 4)]), 4, ((1.2, "chess_play"),))
    print("   chess moves made:", len(g.hist), "last", g.last)
    # Draughts: red a3-b4 (row 5 col 0 to row 4 col 1), the display answers
    g = await play("g_draughts", taps([sq(5, 0), sq(4, 1)]), 3, ((1.5, "draughts_play"),))
    print("   draughts to move:", g.turn)
    # Reversi: dark plays d3, the display answers
    g = await play("g_reversi", taps([sq(2, 3)]), 3, ((2, "reversi_play"),))
    print("   reversi dark %d light %d" % (g.count(1), g.count(2)))
    # Solitaire: turn three cards, tap the pile, tap a column
    import g_solitaire as so
    g = await play("g_solitaire", taps([(so.colx(0) + 20, so.TOP_Y + 30)] * 3 +
                                       [(so.colx(1) + 20, so.TOP_Y + 30), (so.colx(3) + 20, so.TAB_Y + 100)]),
                   5, ((3.6, "solitaire_play"),))
    print("   solitaire pack %d pile %d" % (len(g.stock), len(g.waste)))
    # Sudoku: pick the first empty square, enter a 5
    import g_sudoku as su
    g = await play("g_sudoku", taps([(su.GX + 15, su.GY + 15), (su.PAD_X + 140, su.GY + 70)]), 2.5,
                   ((1.8, "sudoku_play"),))
    print("   sudoku level", su.LEVELS[g.level])


asyncio.run(main())
