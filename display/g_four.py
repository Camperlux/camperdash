# Four in a Row: drop counters into the frame; four in a line wins - across,
# down or diagonally. Two players taking turns on the one display, or one
# against the display, which takes a win when it sees one, blocks yours, and
# otherwise favours the middle and looks one move ahead so as not to hand
# you a win. Tap a column to drop.

import asyncio
import random
import time

import gfx
from arcade import (Base, W, H, HUD_H, C, BG, PANEL, TXT, MUTED, GOLD, RED)

COLS, ROWS = 7, 6
CELL = 40
FX = 20
FY = HUD_H + 22
FRAME = C(40, 80, 170)
HOLE = C(15, 14, 18)
PIECE = (RED, GOLD)


class Game(Base):
    KEY = "four"
    TITLE = "Four in a Row"

    def new(self):
        self.g = [[None] * COLS for _ in range(ROWS)]
        self.turn = self.first
        self.first = 1 - self.first               # take it in turns to start
        self.winner = None
        self.line = None
        self.full = False

    # --- the rules --------------------------------------------------------------
    def drop_row(self, g, c):
        for r in range(ROWS - 1, -1, -1):
            if g[r][c] is None:
                return r
        return None

    def four(self, g, who):
        """The winning line for who, as four (r, c), or None."""
        for r in range(ROWS):
            for c in range(COLS):
                for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
                    cells = [(r + dr * k, c + dc * k) for k in range(4)]
                    if all(0 <= rr < ROWS and 0 <= cc < COLS and g[rr][cc] == who for rr, cc in cells):
                        return cells
        return None

    def play(self, c):
        r = self.drop_row(self.g, c)
        if r is None:
            return False
        self.g[r][c] = self.turn
        self.sfx("tap")
        self.line = self.four(self.g, self.turn)
        if self.line:
            self.winner = self.turn
            if self.solo and self.turn == 0:
                self.wins += 1
                self.record(self.wins)
            self.sfx("chime" if not self.solo or self.turn == 0 else "bad")
        elif all(self.g[0][k] is not None for k in range(COLS)):
            self.full = True
        else:
            self.turn = 1 - self.turn
        return True

    def cpu_move(self):
        g = self.g
        free = [c for c in range(COLS) if self.drop_row(g, c) is not None]
        for who in (1, 0):                         # win if it can, else block
            for c in free:
                r = self.drop_row(g, c)
                g[r][c] = who
                won = self.four(g, who)
                g[r][c] = None
                if won:
                    return c
        # otherwise: nearest the middle, never one that sets up your win above it
        order = sorted(free, key=lambda c: (abs(c - 3), random.random()))
        for c in order:
            r = self.drop_row(g, c)
            g[r][c] = 1
            danger = False
            if r > 0:
                g[r - 1][c] = 0
                danger = bool(self.four(g, 0))
                g[r - 1][c] = None
            g[r][c] = None
            if not danger:
                return c
        return order[0]

    # --- drawing ----------------------------------------------------------------
    def draw(self):
        fb, f = self.fb, self.f
        who = ("You" if self.turn == 0 else "The display") if self.solo else ("Red" if self.turn == 0 else "Gold")
        self.hud(("%s to play" % who) if self.winner is None and not self.full else "",
                 "1 player" if self.solo else "2 players")
        fb.fill_rect(0, HUD_H, W, H - HUD_H, BG)
        gfx.rrect(fb, FX - 8, FY - 8, COLS * CELL + 16, ROWS * CELL + 16, 12, FRAME)
        for r in range(ROWS):
            for c in range(COLS):
                v = self.g[r][c]
                cx, cy = FX + c * CELL + CELL // 2, FY + r * CELL + CELL // 2
                fb.ellipse(cx, cy, 16, 16, HOLE if v is None else PIECE[v], True)
                if self.line and (r, c) in self.line:
                    fb.ellipse(cx, cy, 6, 6, TXT, True)
        # the side panel: whose turn, the tally, a new game, players
        sx = FX + COLS * CELL + 30
        if self.winner is not None:
            msg = (("You win!" if self.winner == 0 else "The display wins") if self.solo
                   else ("Red wins!" if self.winner == 0 else "Gold wins!"))
            f.md.text(fb, msg, sx, FY + 4, PIECE[self.winner], BG)
        elif self.full:
            f.md.text(fb, "A draw", sx, FY + 4, TXT, BG)
        else:
            fb.ellipse(sx + 16, FY + 16, 14, 14, PIECE[self.turn], True)
            f.sm.text(fb, "Tap a column", sx + 38, FY + 8, MUTED, BG)
        if self.solo:
            f.sm.text(fb, "Your wins %d" % self.wins, sx, FY + 50, GOLD, BG)
        self.button(self.btn["new"], "New game", font=f.md)
        self.button(self.btn["mode"], "2 players" if self.solo else "1 player", font=f.md)
        self.d.show()

    async def run(self, stop):
        if self.begin():
            self.solo = True
            self.first = 0
            self.wins = self.best or 0
            self.new()
        sx = FX + COLS * CELL + 24
        self.btn = {"new": (sx, 200, W - sx - 10, 46), "mode": (sx, 254, W - sx - 10, 46)}
        dirty = True
        think_at = None
        while not stop():
            ev = self.tp.poll()
            if ev and ev[0] == "up":
                x, y = ev[3], ev[4]
                if self.is_exit(x, y):
                    return
                b = self.hit(self.btn, x, y)
                if b == "new":
                    self.new()
                elif b == "mode":
                    self.solo = not self.solo
                    self.new()
                elif (self.winner is None and not self.full and FX <= x < FX + COLS * CELL
                      and not (self.solo and self.turn == 1)):
                    self.play((x - FX) // CELL)
                dirty = True
            # the display's turn, after a moment so it can be seen
            if self.solo and self.turn == 1 and self.winner is None and not self.full:
                if think_at is None:
                    think_at = time.ticks_add(time.ticks_ms(), 600)
                elif time.ticks_diff(time.ticks_ms(), think_at) >= 0:
                    think_at = None
                    self.play(self.cpu_move())
                    dirty = True
            if dirty:
                self.draw()
                dirty = False
            await asyncio.sleep_ms(20)
