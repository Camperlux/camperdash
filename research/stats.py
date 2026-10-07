"""Derived metrics from BMS readings: energy counters, extremes, trend history,
smoothed current and time-to-empty/full estimates.

Daily totals/extremes persist to stats.json so a restart doesn't lose them;
they reset automatically at local midnight.
"""
from __future__ import annotations
import json
import os
import time
from collections import deque
from datetime import date

EMA_TAU_S = 60.0     # smoothing window for current used in time estimates
GAP_LIMIT_S = 30.0   # ignore integration across gaps longer than this
SAVE_EVERY_S = 60.0


class SessionStats:
    def __init__(self, path: str = "stats.json", history_len: int = 2400):
        self.path = path
        self.history: deque = deque(maxlen=history_len)  # (t, V, A, soc, W)
        self._last_t: float | None = None
        self._last_save = 0.0
        self.ema_a: float | None = None
        self._reset(date.today().isoformat())
        self._load()

    # ---- state -----------------------------------------------------------
    def _reset(self, day: str):
        self.day = day
        self.started = time.time()
        self.wh_in = self.wh_out = 0.0
        self.ah_in = self.ah_out = 0.0
        self.ext = {k: [None, None] for k in ("v", "a", "t", "soc")}  # [min,max]

    def _track(self, key, val):
        lo, hi = self.ext[key]
        self.ext[key] = [val if lo is None else min(lo, val),
                         val if hi is None else max(hi, val)]

    # ---- persistence -----------------------------------------------------
    def _load(self):
        try:
            with open(self.path) as f:
                d = json.load(f)
            if d.get("day") == self.day:
                self.started = d.get("started", self.started)
                self.wh_in, self.wh_out = d["wh_in"], d["wh_out"]
                self.ah_in, self.ah_out = d["ah_in"], d["ah_out"]
                self.ext = d["ext"]
        except (OSError, KeyError, ValueError):
            pass

    def _save(self):
        self._last_save = time.time()  # even on failure, don't retry every sample
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump({"day": self.day, "started": self.started,
                           "wh_in": self.wh_in, "wh_out": self.wh_out,
                           "ah_in": self.ah_in, "ah_out": self.ah_out,
                           "ext": self.ext}, f)
            os.replace(tmp, self.path)
        except OSError:
            pass  # persistence is best-effort; never disturb the BLE loop

    # ---- update ----------------------------------------------------------
    def update(self, s: dict):
        if not s.get("connected") or s.get("voltage") is None:
            return
        now = time.time()
        today = date.today().isoformat()
        if today != self.day:
            self._reset(today)

        v, a, soc = s["voltage"], s["current"], s.get("soc")
        w = v * a

        dt = (now - self._last_t) if self._last_t else 0.0
        if 0 < dt <= GAP_LIMIT_S:
            h = dt / 3600.0
            if a > 0:
                self.wh_in += w * h
                self.ah_in += a * h
            else:
                self.wh_out += -w * h
                self.ah_out += -a * h
            alpha = min(1.0, dt / EMA_TAU_S)
            self.ema_a = a if self.ema_a is None else self.ema_a + alpha * (a - self.ema_a)
        else:
            self.ema_a = a
        self._last_t = now

        self._track("v", v)
        self._track("a", a)
        if soc is not None:
            self._track("soc", soc)
        for t in s.get("temps_c") or []:
            self._track("t", t)

        self.history.append((round(now), v, a, soc, round(w, 1)))
        if now - self._last_save > SAVE_EVERY_S:
            self._save()

    # ---- output ----------------------------------------------------------
    def summary(self, s: dict) -> dict:
        tte = ttf = None
        ema = self.ema_a
        if ema is not None and s.get("residual_ah") is not None:
            if ema < -0.1:
                tte = s["residual_ah"] / -ema
            elif ema > 0.1 and s.get("nominal_ah"):
                ttf = (s["nominal_ah"] - s["residual_ah"]) / ema
        return {
            "day": self.day,
            "since": self.started,
            "wh_in": round(self.wh_in, 1), "wh_out": round(self.wh_out, 1),
            "ah_in": round(self.ah_in, 2), "ah_out": round(self.ah_out, 2),
            "ext": self.ext,
            "avg_current": None if ema is None else round(ema, 2),
            "time_to_empty_h": None if tte is None else round(tte, 2),
            "time_to_full_h": None if ttf is None else round(ttf, 2),
        }

    def series(self, max_points: int = 240) -> list:
        pts = list(self.history)
        stride = max(1, len(pts) // max_points)
        return pts[::stride]
