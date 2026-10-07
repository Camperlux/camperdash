# Why is the vector calibration not exact?
#
# Hypothesis: a calibration against gravity cannot observe yaw - a rotation of
# the sensor about the vertical axis leaves the measured gravity vector
# completely unchanged, so no amount of sitting on flat ground reveals it. The
# minimal rotation the code builds is therefore correct up to an unknown yaw.
#
# If that is right then a mounting error that is a pure tilt (a genuine single
# rotation, no yaw) must be corrected exactly, and a mounting error built by
# composing two tilts - which induces a small yaw of its own - must leave a
# residual of roughly that induced yaw, appearing only at large van tilts.

import math
import sys
import types

cfg = types.ModuleType("config")
cfg.LEVEL_SWAP_XY = False
cfg.LEVEL_INVERT_ROLL = False
cfg.LEVEL_INVERT_PITCH = False
sys.modules["config"] = cfg

import time
time.sleep_ms = lambda ms: None
time.ticks_ms = lambda: 0
time.ticks_diff = lambda a, b: a - b

import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pico"))
import level as L

D = math.radians


def rx(a):
    c, s = math.cos(a), math.sin(a)
    return ((1, 0, 0), (0, c, -s), (0, s, c))


def ry(a):
    c, s = math.cos(a), math.sin(a)
    return ((c, 0, s), (0, 1, 0), (-s, 0, c))


def rz(a):
    c, s = math.cos(a), math.sin(a)
    return ((c, -s, 0), (s, c, 0), (0, 0, 1))


def mul(A, B):
    return tuple(tuple(sum(A[i][k] * B[k][j] for k in range(3))
                       for j in range(3)) for i in range(3))


def apply(M, v):
    return tuple(sum(M[i][k] * v[k] for k in range(3)) for i in range(3))


def T(M):
    return tuple(tuple(M[j][i] for j in range(3)) for i in range(3))


def angles(v):
    x, y, z = v
    return (math.degrees(math.atan2(y, math.sqrt(x * x + z * z))),
            math.degrees(math.atan2(-x, math.sqrt(y * y + z * z))))


CASES = [(0, 0), (5, 5), (15, -12), (20, 0), (0, 25), (25, 20), (-30, 10)]


def worst(MOUNT):
    """Largest attitude error over the test tilts, vector cal vs angle cal."""
    wn = wo = 0.0
    g_lvl = apply(T(MOUNT), (0.0, 0.0, 1.0))
    L._cal = {"vec": g_lvl, "roll": 0.0, "pitch": 0.0, "at": 1, "orient": ""}
    L._cal_stale = False
    L._set_rot()
    cal_r, cal_p = L._angles(*g_lvl)
    for a, b in CASES:
        g_van = apply(mul(rx(D(a)), ry(D(b))), (0.0, 0.0, 1.0))
        want = angles(g_van)
        g_sen = apply(T(MOUNT), g_van)
        gn = L._angles(*L._apply_rot(*g_sen))
        raw = L._angles(*g_sen)
        go = (raw[0] - cal_r, raw[1] - cal_p)
        wn = max(wn, math.hypot(gn[0] - want[0], gn[1] - want[1]))
        wo = max(wo, math.hypot(go[0] - want[0], go[1] - want[1]))
    return wn, wo


print("mounting error                         vector cal   angle cal")
print("-" * 64)

# 1. pure single-axis tilts: genuine minimal rotations, no yaw at all
for ang in (2.0, 5.0, 10.0):
    wn, wo = worst(rx(D(ang)))
    print("  %4.1f deg about X only                %8.5f    %8.4f" % (ang, wn, wo))
    assert wn < 1e-9, "pure tilt must be corrected exactly, got %g" % wn

for ang in (3.0, 8.0):
    wn, wo = worst(ry(D(ang)))
    print("  %4.1f deg about Y only                %8.5f    %8.4f" % (ang, wn, wo))
    assert wn < 1e-9, "pure tilt must be corrected exactly, got %g" % wn

print()
# 2. composed tilts: these carry an induced yaw the calibration cannot see
for a, b in ((4.0, -3.0), (10.0, 10.0), (2.0, 2.0)):
    wn, wo = worst(mul(rx(D(a)), ry(D(b))))
    # the yaw induced by composing two small rotations is about a*b/2 in radians
    induced = math.degrees(D(a) * D(b) / 2.0)
    print("  X %4.1f then Y %5.1f (yaw ~%.3f deg)  %8.5f    %8.4f"
          % (a, b, abs(induced), wn, wo))

print()
# 3. prove it directly: add an explicit yaw and watch the residual follow it
print("explicit yaw added to a square mounting:")
for yaw in (0.0, 0.5, 1.0, 2.0, 5.0):
    wn, wo = worst(rz(D(yaw)))
    print("   yaw %4.1f deg                        %8.5f    %8.4f" % (yaw, wn, wo))

print()
print("A yaw-only mounting error leaves gravity untouched, so neither method")
print("can see it and both carry it into the reading at large tilt. Pure tilt")
print("misalignment is corrected exactly by the vector method.")

print()
print("ALL OK")
