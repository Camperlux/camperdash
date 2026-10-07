# The logo page's games: a menu, and what the games share.
#
# Tap the logo three times: the menu - two pages of them, swipe or tap the
# arrows. Each game is its own module, imported when it is chosen and let go
# again when it ends, so only the one being played takes any memory. While any
# of them runs, main.py leaves them the screen and the touch panel; an alarm,
# panic, the alarm clock or the kitchen timer ends the lot at once (stop()).
#
# A game ends only with its X. (Swiping in from the right edge used to pause
# one; with two fingers down in Pong the panel can swap which finger it reports
# first, which looked like that swipe and threw two players out mid-game.)

import asyncio
import json
import sys
import time

import gfx

W, H = 480, 320
HUD_H = 32
C = gfx.rgb
BG = C(15, 14, 18)
HUD = C(22, 20, 27)
PANEL = C(28, 26, 34)
PANEL2 = C(39, 36, 46)
LINE = C(52, 48, 60)
TXT = C(239, 233, 224)
MUTED = C(154, 143, 158)
GOLD = C(211, 169, 74)
RED = C(248, 81, 73)
GREEN = C(63, 185, 80)
BLUE = C(88, 166, 255)
AMBER = C(210, 153, 34)
BLACK = C(0, 0, 0)

HI_FILE = "games_hi.json"
_hi = None


def hi_get(key, default=0):
    global _hi
    if _hi is None:
        try:
            with open(HI_FILE) as f:
                _hi = json.load(f)
        except (OSError, ValueError):
            _hi = {}
    return _hi.get(key, default)


def shuffle(items):
    """Shuffle a list in place. MicroPython's random has no shuffle() - using
    it crashed the board games the moment the display took its turn."""
    import random
    for i in range(len(items) - 1, 0, -1):
        j = random.randrange(i + 1)
        items[i], items[j] = items[j], items[i]


def hi_set(key, v):
    hi_get(key)
    _hi[key] = v
    try:
        with open(HI_FILE, "w") as f:
            json.dump(_hi, f)
    except OSError:
        pass


class Touch:
    """Presses and releases from the panel, one event per call:
    ("down", x, y), ("hold", x, y, ms held), ("up", x0, y0, x1, y1) or None.
    A finger already down when a game starts - the tap that chose it - is
    ignored until it lifts, so it cannot also make the game's first move."""

    def __init__(self, touch):
        self.t = touch
        self.down = self.last = None
        self.t0 = 0
        self.swallow = bool(touch.read())

    def poll(self):
        p = self.t.read()
        if self.swallow:
            if not p:
                self.swallow = False
            return None
        if p:
            if self.down is None:
                self.down = self.last = p
                self.t0 = time.ticks_ms()
                return ("down", p[0], p[1])
            self.last = p
            return ("hold", p[0], p[1], time.ticks_diff(time.ticks_ms(), self.t0))
        if self.down is not None:
            d, l = self.down, self.last
            self.down = None
            return ("up", d[0], d[1], l[0], l[1])
        return None


class Base:
    """What every game has: the screen, the fonts, sounds, the top bar with
    its score and the X that leaves, a best score kept on flash."""
    KEY = ""
    TITLE = ""
    LOWER_BETTER = False            # Memory: fewer moves is better

    def __init__(self, ctx):
        self.d, self.fb, self.f, self.snd = ctx.disp, ctx.disp.fb, ctx.fonts, ctx.sound
        self.touch = ctx.touch            # the panel itself, for games that want both fingers
        self.tp = Touch(ctx.touch)
        self.best = hi_get(self.KEY, 0)

    def begin(self):
        """True for a new game; False when carrying on after a pause (the
        swipe in from the right edge): the game keeps where it was."""
        if getattr(self, "_resume", False):
            self._resume = False
            return False
        return True

    def sfx(self, name):
        if self.snd:
            try:
                self.snd.play(name)
            except Exception:
                pass

    def record(self, v):
        """A finished game's score: kept if it is the best. True if it is."""
        if v and (not self.best or (v < self.best if self.LOWER_BETTER else v > self.best)):
            self.best = v
            hi_set(self.KEY, v)
            return True
        return False

    def hud(self, left, mid=""):
        fb, f = self.fb, self.f
        fb.fill_rect(0, 0, W, HUD_H, HUD)
        f.sm.text(fb, self.TITLE, 10, 9, GOLD, HUD)
        f.sm.text(fb, left, 170, 9, TXT, HUD)
        if mid:
            f.sm.text(fb, mid, W - 56, 9, MUTED, HUD, 2)
        fb.fill_rect(W - 42, 3, 38, 26, PANEL2)
        f.md.text(fb, "X", W - 23, 5, TXT, PANEL2, 1)

    def button(self, rect, label, lit=False, font=None):
        x, y, w, h = rect
        back = GOLD if lit else PANEL2
        gfx.rrect(self.fb, x, y, w, h, 12, back)
        font = font or self.f.lg
        font.text(self.fb, label, x + w // 2, y + (h - font.height) // 2, BLACK if lit else TXT, back, 1)

    @staticmethod
    def hit(rects, x, y):
        """Which of {name: (x, y, w, h)} the point is on, or None."""
        for name, (bx, by, bw, bh) in rects.items():
            if bx <= x < bx + bw and by <= y < by + bh:
                return name
        return None

    @staticmethod
    def is_exit(x, y):
        return x > W - 50 and y < HUD_H + 6

    def panel(self, big, lines):
        fb, f = self.fb, self.f
        x, y, w, h = 80, 70, 320, 60 + 26 * len(lines)
        gfx.rrect(fb, x, y, w, h, 14, HUD)
        fb.rect(x, y, w, h, GOLD)
        f.lg.text(fb, big, W // 2, y + 12, GOLD, HUD, 1)
        for i, (t, col) in enumerate(lines):
            f.sm.text(fb, t, W // 2, y + 56 + i * 26, col, HUD, 1)


class _Ctx:
    def __init__(self, disp, fonts, touch, sound):
        self.disp, self.fonts, self.sound = disp, fonts, sound
        self.touch = touch
        self.away = False                 # never set now: see the note at the top


# (module, title, the key its best score is kept under, "lower" when fewer is better)
GAMES = (("game", "Camp Crossing", None, False),
         ("g_pack", "Pack the Van", "pack", False),
         ("g_snake", "Road Trip Snake", "snake", False),
         ("g_memory", "Campsite Memory", "memory", True),
         ("g_2048", "2048: Camper", "2048", False),
         ("g_midge", "Midge Swatter", "midge", False),
         ("g_pac", "Pac-Camper", "pac", False),
         ("g_ttt", "Noughts & Crosses", "ttt", False),
         ("g_invaders", "Van Invaders", "invaders", False),
         ("g_dash", "Campsite Dash", "dash", False),
         ("g_pong", "Pong", "pong", False),
         ("g_breakout", "Breakout", "breakout", False),
         ("g_blocks", "Falling Blocks", "blocks", False),
         ("g_four", "Four in a Row", "four", False),
         ("g_mines", "Mine-sweeper", "mines", True),
         ("g_chess", "Chess", "chess", False),
         ("g_draughts", "Draughts", "draughts", False),
         ("g_solitaire", "Solitaire", "solitaire", True),
         ("g_reversi", "Reversi", "reversi", False),
         ("g_sudoku", "Sudoku", "sudoku", True))
PER_PAGE = 10
PAGES = (len(GAMES) + PER_PAGE - 1) // PER_PAGE


def _crossing_best():
    try:
        with open("game_hi.json") as f:
            return int(json.load(f).get("hi", 0))
    except (OSError, ValueError, AttributeError):
        return 0


def _pictogram(fb, f, i, x, y):
    """A little picture for each game on the menu, in a 60 x 40 box."""
    if i == 0:                                     # a van and a walker
        gfx.rrect(fb, x + 4, y + 12, 40, 20, 5, C(52, 120, 200))
        fb.fill_rect(x + 8, y + 15, 30, 7, C(236, 232, 210))
        fb.ellipse(x + 13, y + 33, 4, 4, BLACK, True)
        fb.ellipse(x + 36, y + 33, 4, 4, BLACK, True)
        fb.ellipse(x + 52, y + 8, 4, 4, C(236, 190, 150), True)
        fb.fill_rect(x + 49, y + 13, 7, 12, C(230, 120, 40))
    elif i == 1:                                   # packed blocks
        for bx, by, col in ((0, 24, RED), (16, 24, BLUE), (32, 24, GOLD), (16, 8, GREEN), (32, 8, GREEN), (48, 24, AMBER)):
            fb.fill_rect(x + bx + 2, y + by, 14, 14, col)
    elif i == 2:                                   # a van towing trailers
        for k in range(3):
            gfx.rrect(fb, x + k * 17, y + 14, 14, 12, 3, C(236, 232, 210))
        gfx.rrect(fb, x + 50, y + 11, 12, 18, 3, C(226, 110, 40))
        fb.ellipse(x + 30, y + 34, 3, 3, RED, True)
    elif i == 3:                                   # two cards
        gfx.rrect(fb, x + 8, y + 4, 22, 32, 4, GOLD)
        gfx.rrect(fb, x + 32, y + 4, 22, 32, 4, PANEL2)
        fb.ellipse(x + 43, y + 20, 6, 6, C(245, 184, 61), True)
    elif i == 4:                                   # a tile
        gfx.rrect(fb, x + 8, y + 2, 46, 36, 6, C(211, 169, 74))
        f.sm.text(fb, "2048", x + 31, y + 11, BLACK, C(211, 169, 74), 1)
    elif i == 5:                                   # a midge
        fb.ellipse(x + 30, y + 22, 5, 4, BLACK, True)
        fb.ellipse(x + 23, y + 15, 7, 4, C(200, 205, 215), True)
        fb.ellipse(x + 37, y + 15, 7, 4, C(200, 205, 215), True)
    elif i == 6:                                   # a muncher, dots and a gull
        from array import array
        fb.ellipse(x + 16, y + 20, 13, 13, C(245, 205, 40), True)
        fb.poly(x + 16, y + 20, array("h", (0, 0, 14, -9, 14, 9)), PANEL, True)
        for k in range(3):
            fb.fill_rect(x + 34 + k * 8, y + 19, 3, 3, TXT)
        fb.ellipse(x + 56, y + 16, 7, 7, C(90, 170, 255), True)
        fb.fill_rect(x + 49, y + 16, 15, 9, C(90, 170, 255))
    elif i == 8:                                   # an invader over the van
        for dx, dy in ((2, 0), (8, 0), (0, 2), (2, 2), (4, 2), (6, 2), (8, 2), (10, 2), (0, 4), (4, 4), (6, 4), (10, 4)):
            fb.fill_rect(x + 18 + dx * 2, y + 2 + dy * 2, 4, 4, C(120, 230, 120))
        fb.fill_rect(x + 29, y + 20, 2, 7, TXT)
        gfx.rrect(fb, x + 20, y + 28, 22, 10, 3, C(226, 110, 40))
    elif i == 10:                                  # pong: two bats and a ball
        fb.fill_rect(x + 4, y + 8, 5, 22, TXT)
        fb.fill_rect(x + 51, y + 14, 5, 22, TXT)
        fb.fill_rect(x + 27, y + 18, 5, 5, GOLD)
        for k in range(0, 40, 6):
            fb.fill_rect(x + 29, y + k, 1, 3, MUTED)
    elif i == 11:                                  # breakout: bricks, ball, bat
        for r, col in enumerate((RED, AMBER, GREEN)):
            for k in range(5):
                fb.fill_rect(x + 2 + k * 12, y + 2 + r * 7, 10, 5, col)
        fb.fill_rect(x + 28, y + 27, 4, 4, TXT)
        fb.fill_rect(x + 18, y + 35, 22, 4, BLUE)
    elif i == 12:                                  # falling blocks
        for bx, by, col in ((20, 2, C(170, 90, 230)), (28, 2, C(170, 90, 230)), (36, 2, C(170, 90, 230)), (28, 10, C(170, 90, 230)),
                            (4, 30, BLUE), (12, 30, BLUE), (12, 22, BLUE), (28, 30, GREEN), (36, 30, GREEN), (44, 30, RED), (52, 30, RED), (52, 22, RED)):
            fb.fill_rect(x + bx, y + by, 7, 7, col)
    elif i == 13:                                  # four in a row
        gfx.rrect(fb, x + 4, y + 4, 52, 34, 5, C(40, 80, 170))
        for r in range(3):
            for k in range(5):
                col = RED if (r, k) in ((2, 0), (2, 1), (1, 1), (2, 3)) else GOLD if (r, k) in ((2, 2), (1, 2), (0, 2), (1, 0)) else BG
                fb.ellipse(x + 12 + k * 9, y + 12 + r * 10, 3, 3, col, True)
    elif i == 14:                                  # minesweeper: a mine and a flag
        fb.ellipse(x + 20, y + 22, 9, 9, TXT, True)
        for dx, dy in ((0, -13), (0, 13), (-13, 0), (13, 0)):
            fb.line(x + 20, y + 22, x + 20 + dx, y + 22 + dy, TXT)
        fb.vline(x + 44, y + 6, 28, TXT)
        fb.fill_rect(x + 45, y + 6, 11, 8, RED)
        fb.fill_rect(x + 38, y + 33, 14, 3, TXT)
    elif i == 15:                                  # chess: squares and a king
        for r in range(4):
            for k in range(6):
                fb.fill_rect(x + 6 + k * 8, y + 4 + r * 8, 8, 8, C(222, 200, 160) if (r + k) % 2 == 0 else C(150, 110, 75))
        fb.fill_rect(x + 27, y + 2, 2, 8, TXT)
        fb.fill_rect(x + 24, y + 4, 8, 2, TXT)
        fb.ellipse(x + 28, y + 16, 6, 5, TXT, True)
        fb.fill_rect(x + 20, y + 22, 16, 10, TXT)
        fb.fill_rect(x + 17, y + 32, 22, 5, TXT)
    elif i == 16:                                  # draughts: two pieces
        for k, col in ((0, C(245, 240, 225)), (1, RED)):
            fb.ellipse(x + 20 + k * 20, y + 22 - k * 4, 13, 13, C(20, 18, 22), True)
            fb.ellipse(x + 20 + k * 20, y + 21 - k * 4, 12, 12, col, True)
            fb.ellipse(x + 20 + k * 20, y + 21 - k * 4, 8, 8, BLACK)
    elif i == 17:                                  # solitaire: two cards
        gfx.rrect(fb, x + 10, y + 4, 26, 34, 3, C(246, 243, 236))
        gfx.rrect(fb, x + 26, y + 2, 26, 34, 3, C(246, 243, 236))
        fb.rect(x + 26, y + 2, 26, 34, MUTED)
        f.sm.text(fb, "A", x + 31, y + 4, RED, C(246, 243, 236))
        fb.ellipse(x + 39, y + 24, 4, 4, RED, True)
        fb.ellipse(x + 45, y + 24, 4, 4, RED, True)
        fb.fill_rect(x + 37, y + 25, 11, 3, RED)
    elif i == 18:                                  # reversi: the felt and four counters
        fb.fill_rect(x + 10, y + 2, 40, 36, C(30, 110, 70))
        for k, col in ((0, BLACK), (1, TXT), (2, TXT), (3, BLACK)):
            fb.ellipse(x + 21 + (k % 2) * 18, y + 11 + (k // 2) * 18, 7, 7, col, True)
    elif i == 19:                                  # sudoku: a grid with numbers
        for k in range(4):
            fb.fill_rect(x + 12 + k * 12, y + 2, 1, 36, MUTED)
            fb.fill_rect(x + 12, y + 2 + k * 12, 36, 1, MUTED)
        for (cx, cy, t) in ((0, 0, "5"), (2, 1, "3"), (1, 2, "8")):
            f.sm.text(fb, t, x + 18 + cx * 12, y + 3 + cy * 12, TXT, BG, 1)
    elif i == 9:                                   # the camper on the road, and the flag
        fb.fill_rect(x, y + 32, 60, 6, C(70, 70, 78))
        gfx.rrect(fb, x + 4, y + 16, 28, 14, 4, C(236, 232, 210))
        fb.fill_rect(x + 5, y + 24, 26, 3, C(226, 110, 40))
        fb.ellipse(x + 10, y + 31, 3, 3, BLACK, True)
        fb.ellipse(x + 26, y + 31, 3, 3, BLACK, True)
        fb.fill_rect(x + 36, y + 26, 10, 6, C(120, 78, 42))
        fb.vline(x + 52, y + 4, 28, TXT)
        fb.fill_rect(x + 53, y + 4, 8, 6, GREEN)
    else:                                          # a noughts and crosses grid
        for k in (1, 2):
            fb.fill_rect(x + 10 + k * 14, y + 1, 2, 38, MUTED)
            fb.fill_rect(x + 10, y + 1 + k * 13, 42, 2, MUTED)
        gfx.thick_line(fb, x + 12, y + 4, x + 21, y + 12, RED, 3)
        gfx.thick_line(fb, x + 21, y + 4, x + 12, y + 12, RED, 3)
        fb.ellipse(x + 44, y + 33, 5, 5, BLUE)


def _menu(fb, f, page=0):
    fb.fill(BG)
    fb.fill_rect(0, 0, W, HUD_H, HUD)
    f.md.text(fb, "Games", 12, 6, GOLD, HUD)
    fb.fill_rect(W - 42, 3, 38, 26, PANEL2)
    f.md.text(fb, "X", W - 23, 5, TXT, PANEL2, 1)
    arrows = {}
    if PAGES > 1:
        # which page, and the way to the others: swipe, or tap an arrow
        f.sm.text(fb, "%d of %d" % (page + 1, PAGES), 250, 9, MUTED, HUD, 1)
        for name, ax, lab, on in (("prev", 150, "<", page > 0), ("next", 310, ">", page < PAGES - 1)):
            if on:
                fb.fill_rect(ax, 3, 44, 26, PANEL2)
                f.md.text(fb, lab, ax + 22, 5, TXT, PANEL2, 1)
                arrows[name] = (ax - 6, 0, 56, HUD_H)
    tiles = []
    # ten games: five across, two down; each tile its picture, its name under
    # it (on two lines if need be), and the best score
    gap = 6
    tw = (W - 16 - 4 * gap) // 5                     # 88
    th = (H - HUD_H - 12 - gap) // 2                 # 135
    first = page * PER_PAGE
    for i, (mod, title, key, lower) in enumerate(GAMES[first:first + PER_PAGE], first):
        x = 8 + ((i - first) % 5) * (tw + gap)
        y = HUD_H + 6 + ((i - first) // 5) * (th + gap)
        gfx.rrect(fb, x, y, tw, th, 12, PANEL)
        _pictogram(fb, f, i, x + (tw - 60) // 2, y + 12)
        words = title.replace(": ", ": ").replace("-", "- ").split(" ")     # Pac- / Camper
        lines, cur = [], ""
        for wd in words:
            t = (cur + " " + wd).strip() if cur and not cur.endswith("-") else (cur + wd)
            if cur and f.sm.width(t.replace("- ", "-")) > tw - 8:
                lines.append(cur)
                cur = wd
            else:
                cur = t
        lines.append(cur)
        for k, ln in enumerate(lines[:2]):
            f.sm.text(fb, ln.replace("- ", "-"), x + tw // 2, y + 62 + k * 18, TXT, PANEL, 1)
        best = _crossing_best() if key is None else hi_get(key, 0)
        if not best:
            note = "New"
        elif key in ("ttt", "pong", "four", "chess", "draughts", "reversi"):
            note = "%d wins" % best
        elif key in ("mines", "solitaire", "sudoku"):
            note = "Best %d:%02d" % (best // 60, best % 60)       # the quickest
        else:
            note = "Best %d" % best
        f.sm.text(fb, note, x + tw // 2, y + th - 26, GOLD if best else MUTED, PANEL, 1)
        tiles.append((i, (x, y, tw, th)))
    return tiles, arrows


async def run(disp, fonts, touch, sound, stop, paused=None, page=0):
    """The menu until X, or stop(); a chosen game until it ends. With paused -
    (index, game) as returned before - that game carries on first. The menu
    opens at page. Returns (index, game) when a game was paused, else None."""
    import gc
    ctx = _Ctx(disp, fonts, touch, sound)
    go_on = lambda: stop() or ctx.away          # noqa: E731 - a game's stop()
    page = max(0, min(PAGES - 1, page))
    while not stop():
        if paused:
            pick, g = paused
            paused = None
            g.touch = ctx.touch
            g.tp = Touch(ctx.touch)
            g._resume = True
        else:
            tiles, arrows = _menu(disp.fb, fonts, page)
            disp.show()
            tp = Touch(ctx.touch)
            pick = turn = None
            while pick is None and turn is None and not stop():
                ev = tp.poll()
                if ev and ev[0] == "up":
                    x0, y0, x, y = ev[1], ev[2], ev[3], ev[4]
                    if Base.is_exit(x, y):
                        return None
                    if abs(x - x0) > 80 and abs(x - x0) > 2 * abs(y - y0):
                        turn = 1 if x < x0 else -1          # a swipe: the next page, or back
                    elif Base.hit(arrows, x, y):
                        turn = 1 if Base.hit(arrows, x, y) == "next" else -1
                    else:
                        for i, (tx, ty, tw, th) in tiles:
                            if tx <= x < tx + tw and ty <= y < ty + th:
                                pick = i
                await asyncio.sleep_ms(20)
            if turn is not None:
                page = max(0, min(PAGES - 1, page + turn))
                continue
            if pick is None:
                return None
            mod = GAMES[pick][0]
            gc.collect()
            g = None
            try:
                m = __import__(mod)
                g = (m.Game(disp, fonts, ctx.touch, sound) if mod == "game" else m.Game(ctx))
            except Exception as e:
                sys.print_exception(e)
                continue
        mod = GAMES[pick][0]
        ctx.away = False
        try:
            await g.run(go_on)
        except Exception as e:
            sys.print_exception(e)
            ctx.away = False
        if ctx.away:
            ctx.away = False
            # paused: kept for later - but not Camp Crossing, whose game lives
            # inside its run(); leaving that one ends it
            return None if mod == "game" else (pick, g)
        g = None
        sys.modules.pop(mod, None)             # its code and pictures, let go
        gc.collect()
    return None
