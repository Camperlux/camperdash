"""Look for drift in the levelling sensor.

Two jobs:

  python tools/level_drift.py watch [--hub 192.168.4.245] [--every 2] [--out level_watch.csv]
      Poll the hub live and append every reading to a CSV - steady or not -
      along with what the heater is doing. For watching a warm-up as it
      happens; Ctrl+C stops it. The hub's own drift log skips readings taken
      while the van is not still, and the heater's fan can be enough for that.

  python tools/level_drift.py analyse [--hub ...] [--csv file] [--out level_drift.png] [--last 24]
      Download the hub's drift log (or read a CSV written by watch) and chart
      roll and pitch against time and against the sensor's temperature, with
      a straight-line fit of angle on temperature. A clear slope means the
      drift is thermal and can be compensated in software; angle wandering
      with no link to temperature points at the mounting instead. --last keeps
      only the final N hours.

  python tools/level_drift.py trip [--hub ...] [--csv trip.csv] [--out trip.png] [--last 300]
      The hub's record of the last 15 minutes or so (GET /api/level/trip) - kept
      on the hub, so a drive out of Wi-Fi range is still all there - charted
      as the G-force view shows it: forward g = tan(pitch), cornering g =
      tan(roll), bumps = the total's departure from its running 1 g. --last
      keeps only the final N seconds.
"""

import argparse
import csv
import io
import json
import os
import sys
import time
import urllib.request
from datetime import datetime

LOG_COLS = ["t", "temp_c", "roll", "pitch", "raw_roll", "raw_pitch", "ax", "ay", "az"]


def get(hub, path, timeout=8):
    with urllib.request.urlopen("http://%s%s" % (hub, path), timeout=timeout) as r:
        return r.read()


# --- watch -------------------------------------------------------------------
def watch(a):
    new = not os.path.exists(a.out)
    f = open(a.out, "a", newline="")
    w = csv.writer(f)
    if new:
        w.writerow(LOG_COLS + ["steady", "off_by", "heater_mode", "heater_on", "heater_fan",
                               "cabin_c", "bms_c"])
    print("writing to %s every %ss - Ctrl+C to stop" % (a.out, a.every))
    print("%-8s %6s %7s %7s %7s %6s  %s" % ("time", "temp", "roll", "pitch", "off_by", "still", "heater"))
    last_data = 0
    heat = {}
    bms_c = ""
    try:
        while True:
            t0 = time.time()
            try:
                lv = json.loads(get(a.hub, "/api/level"))["level"]
                # the full status is bigger; once every 20 s is plenty for the heater
                if t0 - last_data > 20:
                    d = json.loads(get(a.hub, "/api/data", timeout=15))
                    heat = d.get("heater") or {}
                    temps = d.get("temps_c") or []
                    bms_c = "%.1f" % (sum(temps) / len(temps)) if temps else ""
                    last_data = t0
                raw = lv.get("raw") or {}
                ax = lv.get("axes") or {}
                row = [int(t0), lv.get("temp_c"), lv.get("roll"), lv.get("pitch"),
                       raw.get("roll"), raw.get("pitch"), ax.get("x"), ax.get("y"), ax.get("z"),
                       int(bool(lv.get("steady"))), lv.get("off_by"),
                       heat.get("mode", ""), int(bool(heat.get("on"))), heat.get("fan_level", ""),
                       heat.get("air_temp_c", ""), bms_c]
                w.writerow(row)
                f.flush()
                print("%-8s %6s %7s %7s %7s %6s  %s" % (
                    datetime.fromtimestamp(t0).strftime("%H:%M:%S"), lv.get("temp_c"),
                    lv.get("roll"), lv.get("pitch"), lv.get("off_by"),
                    "yes" if lv.get("steady") else "no",
                    heat.get("mode", "?") + (" fan %s" % heat.get("fan_level") if heat.get("on") else "")))
            except Exception as e:                     # the hub busy or out of reach: keep going
                print("  (no reading: %s)" % e)
            time.sleep(max(0.2, a.every - (time.time() - t0)))
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        f.close()


# --- analyse -----------------------------------------------------------------
def fit(xs, ys):
    """Least-squares line: slope, intercept, r."""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if not sxx or not syy:
        return 0.0, my, 0.0
    return sxy / sxx, my - sxy / sxx * mx, sxy / (sxx * syy) ** 0.5


def load_rows(a):
    if a.csv:
        text = open(a.csv, encoding="utf-8").read()
        src = a.csv
    else:
        text = get(a.hub, "/api/level/log", timeout=30).decode("utf-8", "replace")
        src = "the hub"
        # kept beside the chart, for a second look without the hub
        with open(os.path.splitext(a.out)[0] + ".csv", "w", encoding="utf-8") as f:
            f.write(text)
    rd = csv.reader(io.StringIO(text))
    rows = []
    for r in rd:
        if not r or not r[0].strip().isdigit():
            continue                                   # header or "no log yet"
        try:
            rows.append([float(v) if v not in ("", None) else None for v in r[:9]])
        except ValueError:
            continue
    # the hub's log is a ring, so its rows are not always in time order
    rows.sort(key=lambda r: r[0])
    if getattr(a, "last", None) and a.job == "analyse" and rows:
        rows = [r for r in rows if r[0] >= rows[-1][0] - a.last * 3600]
    return rows, src


def analyse(a):
    rows, src = load_rows(a)
    if len(rows) < 5:
        sys.exit("only %d reading(s) from %s - leave it logging for longer" % (len(rows), src))
    t = [datetime.fromtimestamp(r[0]) for r in rows]
    temp = [r[1] for r in rows]
    roll = [r[2] for r in rows]
    pitch = [r[3] for r in rows]
    hours = (rows[-1][0] - rows[0][0]) / 3600

    print("%d readings over %.1f h from %s" % (len(rows), hours, src))
    print("sensor temperature  %5.1f .. %5.1f C  (range %.1f)" % (min(temp), max(temp), max(temp) - min(temp)))
    out = {}
    for name, ys in (("roll", roll), ("pitch", pitch)):
        s, c, r = fit(temp, ys)
        resid = [y - (s * x + c) for x, y in zip(temp, ys)]
        out[name] = (s, c, r)
        print("%-5s %6.2f .. %6.2f deg  (range %.2f)   vs temp: %+.4f deg/C, r = %+.2f, "
              "left after removing temp: %.2f deg"
              % (name, min(ys), max(ys), max(ys) - min(ys), s, r, max(resid) - min(resid)))
    print()
    for name in ("roll", "pitch"):
        s, c, r = out[name]
        ys = roll if name == "roll" else pitch
        if max(ys) - min(ys) < 0.25:
            print("%s: steady - it moved only %.2f deg, which is reading noise" % (name, max(ys) - min(ys)))
        elif max(temp) - min(temp) < 3:
            print("%s: the temperature has hardly changed (under 3 C) - too early to say" % name)
        elif abs(r) >= 0.7:
            print("%s: follows temperature closely - compensation would remove most of it" % name)
        elif abs(r) >= 0.4:
            print("%s: partly temperature - compensation would help, something else is moving too" % name)
        else:
            print("%s: not explained by temperature - look at the mounting" % name)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as md
    fig, ax = plt.subplots(2, 2, figsize=(13, 8))
    ax[0][0].plot(t, roll, label="roll")
    ax[0][0].plot(t, pitch, label="pitch")
    ax[0][0].set_ylabel("degrees")
    ax[0][0].set_title("Angle over time")
    ax[0][0].legend()
    ax[1][0].plot(t, temp, color="tab:red")
    ax[1][0].set_ylabel("sensor °C")
    ax[1][0].set_title("Sensor temperature over time")
    for x in (ax[0][0], ax[1][0]):
        x.xaxis.set_major_formatter(md.DateFormatter("%a %H:%M" if hours > 20 else "%H:%M"))
        x.grid(alpha=.3)
    for i, (name, ys) in enumerate((("roll", roll), ("pitch", pitch))):
        s, c, r = out[name]
        p = ax[i][1]
        p.scatter(temp, ys, s=8, c=[(r_[0] - rows[0][0]) / 3600 for r_ in rows], cmap="viridis")
        lo, hi = min(temp), max(temp)
        p.plot([lo, hi], [s * lo + c, s * hi + c], color="k", lw=1)
        p.set_title("%s vs temperature: %+.4f °/°C, r = %+.2f" % (name, s, r))
        p.set_xlabel("sensor °C")
        p.set_ylabel("degrees")
        p.grid(alpha=.3)
    fig.suptitle("Levelling sensor drift · %d readings over %.1f h (colour = hours from start)" % (len(rows), hours))
    fig.tight_layout()
    fig.savefig(a.out, dpi=110)
    print("\nchart: %s" % os.path.abspath(a.out))


def trip(a):
    import math
    if a.csv:
        text = open(a.csv, encoding="utf-8").read()
    else:
        text = get(a.hub, "/api/level/trip", timeout=60).decode("utf-8", "replace")
        with open(os.path.splitext(a.out)[0] + ".csv", "w", encoding="utf-8") as f:
            f.write(text)
    # when the hub was held up during the trip, and by what (pico/stalls.py)
    stall_list = []
    if not a.csv:
        try:
            stall_list = json.loads(get(a.hub, "/api/stalls"))["stalls"]
        except Exception as e:                         # an older hub has none
            print("(no stall record: %s)" % e)
    rows = []
    for r in csv.reader(io.StringIO(text)):
        try:
            rows.append([float(v) for v in r[:5]])
        except (ValueError, IndexError):
            continue                                   # the header
    if not rows:
        sys.exit("no readings - is the hub's level sensor on?")
    if a.last:
        end = rows[-1][1]
        rows = [r for r in rows if r[1] >= end - a.last * 1000]
    t = [(r[1] - rows[0][1]) / 1000 for r in rows]
    fwd = [math.tan(math.radians(r[3])) for r in rows]
    cor = [math.tan(math.radians(r[2])) for r in rows]
    base, bump = None, []
    for r in rows:
        base = r[4] if base is None else base + 0.02 * (r[4] - base)
        bump.append(r[4] - base)
    gaps = sum(1 for i in range(1, len(t)) if t[i] - t[i - 1] > 1.0)
    print("%d readings over %.0f s (%s), %d gap(s) over 1 s"
          % (len(rows), t[-1], datetime.fromtimestamp(rows[0][0]).strftime("%H:%M:%S"), gaps))
    print("forward   accel %.2f g  brake %.2f g" % (max(fwd), -min(fwd)))
    print("cornering right %.2f g  left  %.2f g" % (max(cor), -min(cor)))
    print("bumps     %.2f g" % max(abs(v) for v in bump))
    t_first, t_last = rows[0][0], rows[-1][0]
    held = [s for s in stall_list if t_first - 5 <= s["t"] <= t_last + 5]
    for s in held:
        print("held up %.1f s at %s during %s (wifi %s, hotspot %s)" % (
            s["ms"] / 1000, datetime.fromtimestamp(s["t"]).strftime("%H:%M:%S"),
            ", ".join(s["during"]) or "?", s["wifi"], s["hotspot"]))

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(3, 1, figsize=(13, 8), sharex=True)
    for p_, ys, lab, col in ((ax[0], fwd, "forward g (+ accel, - brake)", "tab:green"),
                             (ax[1], cor, "cornering g (+ right, - left)", "tab:orange"),
                             (ax[2], bump, "bumps g", "tab:blue")):
        p_.plot(t, ys, color=col, lw=1)
        p_.axhline(0, color="k", lw=.6)
        p_.set_ylabel(lab)
        p_.grid(alpha=.3)
    ax[2].set_xlabel("seconds from %s" % datetime.fromtimestamp(rows[0][0]).strftime("%H:%M:%S"))
    for s in held:                                     # the stalls, shaded
        end = s["t"] - t_first
        for p_ in ax:
            p_.axvspan(end - s["ms"] / 1000, end, color="tab:red", alpha=0.15)
    fig.suptitle("Trip: %d readings over %.0f s%s" % (
        len(rows), t[-1], ("; %d hold-up(s) shaded" % len(held)) if held else ""))
    fig.tight_layout()
    fig.savefig(a.out, dpi=100)
    print("chart: %s" % os.path.abspath(a.out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("job", choices=("watch", "analyse", "trip"))
    ap.add_argument("--hub", default="192.168.4.245")
    ap.add_argument("--every", type=float, default=2.0, help="watch: seconds between readings")
    ap.add_argument("--csv", help="analyse: a CSV from watch instead of the hub's log")
    ap.add_argument("--out", help="watch: CSV to append to; analyse, trip: chart PNG")
    ap.add_argument("--last", type=float, help="trip: only the final N seconds; analyse: the final N hours")
    a = ap.parse_args()
    if a.job == "watch":
        a.out = a.out or "level_watch.csv"
        watch(a)
    elif a.job == "trip":
        a.out = a.out or "trip.png"
        trip(a)
    else:
        a.out = a.out or "level_drift.png"
        analyse(a)


if __name__ == "__main__":
    main()
