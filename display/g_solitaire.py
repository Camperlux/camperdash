# Solitaire (Klondike, dealing one card at a time).
#
# Build the four foundations up from Ace to King in each suit. On the seven
# columns, cards go down in alternating colours; only a King fills an empty
# column. Tap a card (or the top of a run of face-up cards), then tap where it
# goes; tap the selected card again to send it to its foundation. Tap the pack
# to turn a card; when it is empty, tap it to turn the pile back over. Once
# every card is face up, the rest plays itself out. Best: the quickest win.

import asyncio
import random
import time
from array import array

import gfx
from arcade import (Base, W, H, HUD_H, HUD, C, BG, PANEL2, TXT, MUTED, GOLD, BLACK)

CW, CH = 62, 84
GAP = 5
X0 = (W - (7 * CW + 6 * GAP)) // 2
TOP_Y = HUD_H + 4
TAB_Y = TOP_Y + CH + 8
FACE = C(246, 243, 236)
BACK = C(45, 90, 170)
REDC = C(210, 45, 45)
SEL = C(90, 200, 120)
RANKS = ("", "A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
NEW_BTN = (W - 120, 3, 70, 26)


def colx(i):
    return X0 + i * (CW + GAP)


def red(card):
    return card[1] < 2          # hearts, diamonds


def suit(fb, cx, cy, s, r, col):
    """A suit mark, about 2r across."""
    if s == 0:                                            # heart
        fb.ellipse(cx - r // 2, cy - r // 3, r // 2 + 1, r // 2 + 1, col, True)
        fb.ellipse(cx + r // 2, cy - r // 3, r // 2 + 1, r // 2 + 1, col, True)
        fb.poly(0, 0, array("h", (cx - r, cy - r // 4, cx + r, cy - r // 4, cx, cy + r)), col, True)
    elif s == 1:                                          # diamond
        fb.poly(0, 0, array("h", (cx, cy - r, cx + r * 3 // 4, cy, cx, cy + r, cx - r * 3 // 4, cy)), col, True)
    elif s == 2:                                          # club
        q = r // 2 + 1
        fb.ellipse(cx, cy - r // 2, q, q, col, True)
        fb.ellipse(cx - r // 2 - 1, cy + r // 5, q, q, col, True)
        fb.ellipse(cx + r // 2 + 1, cy + r // 5, q, q, col, True)
        fb.fill_rect(cx - 1, cy, 3, r, col)
    else:                                                 # spade
        fb.poly(0, 0, array("h", (cx, cy - r, cx + r, cy + r // 4, cx - r, cy + r // 4)), col, True)
        fb.ellipse(cx - r // 2, cy + r // 4, r // 2 + 1, r // 2 + 1, col, True)
        fb.ellipse(cx + r // 2, cy + r // 4, r // 2 + 1, r // 2 + 1, col, True)
        fb.fill_rect(cx - 1, cy, 3, r, col)


class Game(Base):
    KEY = "solitaire"
    TITLE = "Solitaire"
    LOWER_BETTER = True

    def new(self):
        deck = [(r, s) for s in range(4) for r in range(1, 14)]
        for i in range(len(deck) - 1, 0, -1):          # a fair shuffle
            j = random.randrange(i + 1)
            deck[i], deck[j] = deck[j], deck[i]
        self.tab = []
        for c in range(7):
            col = []
            for k in range(c + 1):
                col.append([deck.pop(), k == c])
            self.tab.append(col)
        self.stock = deck
        self.waste = []
        self.found = [[] for _ in range(4)]
        self.sel = None           # ("w",) / ("t", col, index) / ("f", suit)
        self.t0 = None
        self.won = False
        self.secs = 0

    # --- the rules ----------------------------------------------------------------
    def picked(self):
        """The cards the selection carries (a list), or []."""
        s = self.sel
        if not s:
            return []
        if s[0] == "w":
            return self.waste[-1:]
        if s[0] == "f":
            return self.found[s[1]][-1:]
        return [c for c, up in self.tab[s[1]][s[2]:]]

    def can_found(self, card):
        f = self.found[card[1]]
        return card[0] == (f[-1][0] + 1 if f else 1)

    def can_tab(self, card, col):
        t = self.tab[col]
        if not t:
            return card[0] == 13
        top, up = t[-1]
        return up and top[0] == card[0] + 1 and red(top) != red(card)

    def take(self):
        """Remove the selection from where it was."""
        s = self.sel
        if s[0] == "w":
            self.waste.pop()
        elif s[0] == "f":
            self.found[s[1]].pop()
        else:
            t = self.tab[s[1]]
            while len(t) > s[2]:
                t.pop()
            if t and not t[-1][1]:
                t[-1][1] = True                       # the card under it turns over
        self.sel = None

    def to_found(self):
        cards = self.picked()
        if len(cards) == 1 and self.sel[0] != "f" and self.can_found(cards[0]):
            self.take()
            self.found[cards[0][1]].append(cards[0])
            self.sfx("ok")
            self.check()
            return True
        return False

    def to_tab(self, col):
        cards = self.picked()
        if cards and not (self.sel[0] == "t" and self.sel[1] == col) and self.can_tab(cards[0], col):
            self.take()
            self.tab[col].extend([c, True] for c in cards)
            self.sfx("tap")
            return True
        return False

    def check(self):
        if sum(len(f) for f in self.found) == 52:
            self.won = True
            self.secs = time.ticks_diff(time.ticks_ms(), self.t0) // 1000 if self.t0 else 0
            self.record(max(1, self.secs))
            self.sfx("chime")

    def all_up(self):
        return not self.stock and not self.waste and all(up for col in self.tab for c, up in col)

    def auto_step(self):
        """Once everything is face up: one card home. False when none can go."""
        for i, col in enumerate(self.tab):
            if col and self.can_found(col[-1][0]):
                self.sel = ("t", i, len(col) - 1)
                return self.to_found()
        return False

    # --- taps ---------------------------------------------------------------------
    def tap(self, x, y):
        if self.t0 is None:
            self.t0 = time.ticks_ms()
        # the top row: pack, pile, foundations
        if TOP_Y <= y < TOP_Y + CH:
            if colx(0) <= x < colx(0) + CW:
                self.sel = None
                if self.stock:
                    self.waste.append(self.stock.pop())
                else:
                    self.stock = self.waste[::-1]
                    self.waste = []
                self.sfx("page")
                return
            if colx(1) <= x < colx(1) + CW and self.waste:
                if self.sel == ("w",):
                    if not self.to_found():
                        self.sel = None
                else:
                    self.sel = ("w",)
                return
            for s in range(4):
                if colx(3 + s) <= x < colx(3 + s) + CW:
                    if self.sel and self.sel[0] != "f":
                        cards = self.picked()
                        if len(cards) == 1 and cards[0][1] == s:
                            self.to_found()
                            return
                    self.sel = ("f", s) if self.found[s] and self.sel != ("f", s) else None
                    return
            return
        # the columns
        if y >= TAB_Y:
            for col in range(7):
                if colx(col) <= x < colx(col) + CW:
                    self.tap_column(col, y)
                    return
        self.sel = None

    def tap_column(self, col, y):
        t = self.tab[col]
        i = self.card_at(col, y)
        if self.sel and (self.sel[0] != "t" or self.sel[1] != col):
            if self.to_tab(col):
                return
        if i is None:
            self.sel = None
            return
        if not t[i][1]:
            if i == len(t) - 1:
                t[i][1] = True                         # turn over the top card
                self.sfx("page")
            self.sel = None
            return
        if self.sel == ("t", col, i):
            if not self.to_found():
                self.sel = None
            return
        self.sel = ("t", col, i)

    def offsets(self, col):
        """Where each card of a column starts, packed to fit the screen."""
        t = self.tab[col]
        down = sum(1 for c, up in t if not up)
        ups = len(t) - down
        room = H - 4 - TAB_Y - CH - down * 7
        step = min(24, room // (ups - 1)) if ups > 1 else 24
        out, y = [], TAB_Y
        for c, up in t:
            out.append(y)
            y += step if up else 7
        return out

    def card_at(self, col, y):
        ys = self.offsets(col)
        if not ys:
            return None
        for i in range(len(ys) - 1, -1, -1):
            if y >= ys[i]:
                return i if y < ys[i] + CH else (len(ys) - 1 if i == len(ys) - 1 else None)
        return None

    # --- drawing ------------------------------------------------------------------
    def card(self, x, y, card, up=True, lit=False):
        fb, f = self.fb, self.f
        if not up:
            gfx.rrect(fb, x, y, CW, CH, 5, BACK)
            fb.rect(x + 4, y + 4, CW - 8, CH - 8, C(90, 130, 200))
            return
        gfx.rrect(fb, x, y, CW, CH, 5, SEL if lit else FACE)
        if lit:
            gfx.rrect(fb, x + 2, y + 2, CW - 4, CH - 4, 4, FACE)
        col = REDC if red(card) else BLACK
        f.md.text(fb, RANKS[card[0]], x + 5, y + 3, col, FACE)
        suit(fb, x + CW - 12, y + 14, card[1], 6, col)
        suit(fb, x + CW // 2, y + CH // 2 + 12, card[1], 14, col)

    def slot(self, x, y, label=""):
        self.fb.rect(x, y, CW, CH, PANEL2)
        if label:
            self.f.sm.text(self.fb, label, x + CW // 2, y + CH // 2 - 8, MUTED, BG, 1)

    def draw(self):
        fb, f = self.fb, self.f
        secs = self.secs if self.won else (time.ticks_diff(time.ticks_ms(), self.t0) // 1000 if self.t0 else 0)
        self.hud("%d:%02d" % (secs // 60, secs % 60), "")
        x, y, w, h = NEW_BTN
        fb.fill_rect(x, y, w, h, PANEL2)
        f.sm.text(fb, "New", x + w // 2, y + 4, TXT, PANEL2, 1)
        if self.best:
            f.sm.text(fb, "Best %d:%02d" % (self.best // 60, self.best % 60), 250, 9, GOLD, HUD)
        fb.fill_rect(0, HUD_H, W, H - HUD_H, BG)
        # pack and pile
        if self.stock:
            self.card(colx(0), TOP_Y, None, False)
        else:
            self.slot(colx(0), TOP_Y, "again")
        if self.waste:
            self.card(colx(1), TOP_Y, self.waste[-1], True, self.sel == ("w",))
        else:
            self.slot(colx(1), TOP_Y)
        for s in range(4):
            fx = colx(3 + s)
            if self.found[s]:
                self.card(fx, TOP_Y, self.found[s][-1], True, self.sel == ("f", s))
            else:
                self.slot(fx, TOP_Y)
                suit(fb, fx + CW // 2, TOP_Y + CH // 2, s, 12, PANEL2)
        # the columns
        for col in range(7):
            t = self.tab[col]
            if not t:
                self.slot(colx(col), TAB_Y, "K")
            for i, (y, (card, up)) in enumerate(zip(self.offsets(col), t)):
                lit = self.sel is not None and self.sel[0] == "t" and self.sel[1] == col and i >= self.sel[2]
                self.card(colx(col), y, card, up, lit)
        if self.won:
            self.panel("You won!", (("%d:%02d%s" % (self.secs // 60, self.secs % 60,
                                                    " - a new best!" if self.secs == self.best else ""), TXT),
                                    ("Tap for a new deal", MUTED)))
        self.d.show()

    async def run(self, stop):
        if self.begin():
            self.new()
        dirty = True
        shown = -1
        auto_at = 0
        while not stop():
            ev = self.tp.poll()
            if ev and ev[0] == "up":
                x, y = ev[3], ev[4]
                if self.is_exit(x, y):
                    return
                if self.hit({"n": NEW_BTN}, x, y) or self.won:
                    self.new()
                else:
                    self.tap(x, y)
                dirty = True
            # everything face up: the rest goes home by itself
            if not self.won and self.all_up() and time.ticks_diff(time.ticks_ms(), auto_at) >= 0:
                auto_at = time.ticks_add(time.ticks_ms(), 120)
                if self.auto_step():
                    dirty = True
            if self.t0 and not self.won:
                s = time.ticks_diff(time.ticks_ms(), self.t0) // 1000
                if s != shown:
                    shown, dirty = s, True
            if dirty:
                self.draw()
                dirty = False
            await asyncio.sleep_ms(20)
