# Vehicle levelling from an accelerometer: the GY-521 (MPU-6050), or an
# LIS3DH breakout (the RP2350 hub's) - whichever answers; find() says which.
#
# Only the accelerometer is used. The van is stationary when you level it, so
# what is wanted is the direction of gravity - no sensor fusion, and none of the
# gyro's drift. At +/-2g the part resolves about 0.004 degrees, so resolution
# has never been the limit here; bandwidth, mounting and temperature are.
#
# Three things matter for accuracy, and all three are dealt with below:
#
#   Bandwidth.  The MPU-6050 powers up with its low-pass filter switched off,
#   which leaves the accelerometer at 260 Hz while this code samples it every
#   4 ms (250 Hz). Everything above 125 Hz then aliases into the average as a
#   bias rather than as visible noise - and a van is full of it: the fridge
#   compressor, the water pump, wind, somebody moving about inside. The filter
#   is now configured explicitly in init().
#
#   Mounting.  The sensor will never be square to the van, so calibration
#   records the direction of gravity on known-flat ground and every later
#   reading is rotated into the van's frame. This is not the same as measuring
#   the resting angles and subtracting them: rotations do not commute, so
#   subtracting a fixed roll and pitch is only correct while both the
#   misalignment and the tilt are small. With the scale now running to 30
#   degrees that approximation was worth a few tenths of a degree.
#
#   Temperature.  The zero-g point moves about 0.5 mg/degC, which is 0.03
#   degrees of apparent tilt per degree C - roughly a degree across the range a
#   dashboard sees between a cold night and a sunny afternoon. Nothing here can
#   remove that, but the die temperature is read alongside every sample and can
#   be logged, so the drift can be measured instead of guessed at.

import json
import math
import time

import config as cfg

ADDR = 0x68                  # the fitted sensor's address, set by find()
CHIP = "mpu6050"             # or "lis3dh"
_PWR_MGMT_1 = 0x6B
_CONFIG = 0x1A               # DLPF_CFG lives here - see init()
_ACCEL_CONFIG = 0x1C
_ACCEL_XOUT_H = 0x3B
_WHO_AM_I = 0x75

CAL_FILE = "level_cal.json"
LOG_FILE = "level_log.csv"
SAMPLES = 16                 # ~16 reads, averaged
LSB_PER_G = 16384.0          # +/-2g full scale

# The LIS3DH (ST). Its 12-bit high-resolution samples come left-justified in 16
# bits, 1 mg a count at +/-2g: 16000 to the g. It reads out in the order low
# byte, high byte; setting the address's top bit makes it step through the
# registers, so all three axes come in one read.
_LIS_ADDRS = (0x19, 0x18)    # SDO/SA0 high, low
_LIS_WHO = 0x33
_LIS_CTRL1 = 0x20
_LIS_CTRL4 = 0x23
_LIS_TEMP_CFG = 0x1F
_LIS_OUT = 0x28 | 0x80
_LIS_ADC3 = 0x0C | 0x80
_LIS_LSB_PER_G = 16000.0

_i2c = None
_ok = False
_err = ""
# vec is the direction of gravity, in the sensor's own axes, measured on flat
# ground. Everything else in here is for display: the rotation is what is
# actually applied. orient is recorded only so the page can show which axis
# mapping was in force - unlike the old angle-domain zero, a vector calibration
# stays valid when that mapping changes, because swapping and inverting axes
# relabels the output rather than moving the sensor.
_cal = {"vec": None, "roll": 0.0, "pitch": 0.0, "at": 0, "orient": ""}
_cal_stale = False
_cal_reason = ""
# The rotation carrying _cal["vec"] onto +Z, worked out once when the
# calibration is loaded rather than per reading: None for no correction, the
# string "flip" for a sensor mounted upside down, otherwise (kx, ky, kz, cos,
# sin) for Rodrigues below.
_rot = None
# Scratch buffer reused for every read: this is polled continuously and a fresh
# bytearray each time is exactly the churn that fragmented this heap before.
# Eight bytes, not six - 0x3B..0x42 is the three accelerometer axes followed by
# the die temperature, so one burst read gets both and the temperature costs no
# extra bus traffic.
_buf = bytearray(8)
_lisbuf = bytearray(6)
_listmp = bytearray(2)
# Last reading, kept so several callers within a moment of each other share one
# measurement. A read is 16 samples at 4 ms - about 80 ms of blocking I2C - and
# it was being done on every /api/data request, from every page, whether or not
# anything was showing the level. Two browsers on the overview page alone had
# the hub measuring tilt several times a second for nobody.
_last = None
_last_ms = 0
# Drift log bookkeeping
_log_rows = 0
_log_last_t = 0
_log_counted = False
# Widest one-axis spread, in g, across the reads behind the last average
_spread = 0.0
# Still means the reads behind one average agree to within this, in g. With the
# MPU-6050's 5 Hz filter a parked van's noise is a few mg; somebody moving about
# inside is tens.
STILL_SPREAD_G = 0.03
# Calibration takes this many averages this far apart (about 1.5 s in all), so
# a slow rock - too slow to show inside one 64 ms average - is caught too.
CAL_BLOCKS = 8
CAL_GAP_MS = 120


def _s16(hi, lo):
    v = (hi << 8) | lo
    return v - 65536 if v > 32767 else v


def _orient():
    """A short signature of the current axis mapping."""
    return "%d%d%d" % (1 if getattr(cfg, "LEVEL_SWAP_XY", False) else 0,
                       1 if getattr(cfg, "LEVEL_INVERT_ROLL", False) else 0,
                       1 if getattr(cfg, "LEVEL_INVERT_PITCH", False) else 0)


# --- calibration store -------------------------------------------------------
def _set_rot():
    """Work out the rotation that carries the stored level vector onto +Z.

    The minimal one: the axis is perpendicular to both, so no yaw is invented.
    That is what we want - there is no yaw reference here and none is needed,
    because roll and pitch are both measured against gravity.
    """
    global _rot
    _rot = None
    v = _cal.get("vec")
    if not v:
        return
    vx, vy, vz = v
    n = math.sqrt(vx * vx + vy * vy + vz * vz)
    if n < 1e-6:
        return
    vx, vy, vz = vx / n, vy / n, vz / n
    c = vz                                   # cosine of the angle to +Z
    s = math.sqrt(vx * vx + vy * vy)         # and its sine
    if s < 1e-9:
        # Already square to +Z, or exactly inverted. The rotation axis is
        # undefined at both ends, so they are handled explicitly rather than by
        # dividing by zero: upright needs nothing, upside down is a half turn.
        _rot = None if c > 0 else "flip"
        return
    # axis = v x Z = (vy, -vx, 0), whose length is exactly s
    _rot = (vy / s, -vx / s, 0.0, c, s)


def _apply_rot(x, y, z):
    """Rotate a raw sensor reading into the van's frame."""
    if _rot is None:
        return x, y, z
    if _rot == "flip":
        return x, -y, -z
    kx, ky, kz, c, s = _rot
    # Rodrigues: v cos + (k x v) sin + k (k.v)(1 - cos). kz is always zero for
    # the axis built above, but the general form is written out so this stays
    # correct if that ever stops being true.
    cx = ky * z - kz * y
    cy = kz * x - kx * z
    cz = kx * y - ky * x
    d = (kx * x + ky * y + kz * z) * (1.0 - c)
    return (x * c + cx * s + kx * d,
            y * c + cy * s + ky * d,
            z * c + cz * s + kz * d)


def load_cal():
    global _cal, _cal_stale, _cal_reason
    _cal = {"vec": None, "roll": 0.0, "pitch": 0.0, "at": 0, "orient": "", "chip": CHIP}
    _cal_stale = False
    _cal_reason = ""
    try:
        with open(CAL_FILE) as f:
            d = json.load(f)
        if isinstance(d, dict):
            v = d.get("vec")
            if isinstance(v, (list, tuple)) and len(v) == 3:
                v = (float(v[0]), float(v[1]), float(v[2]))
            else:
                v = None
            _cal = {"vec": v,
                    "roll": float(d.get("roll", 0.0)),
                    "pitch": float(d.get("pitch", 0.0)),
                    "at": int(d.get("at", 0)),
                    "orient": str(d.get("orient", "")),
                    # older files are all the MPU-6050's
                    "chip": str(d.get("chip", "mpu6050"))}
    except (OSError, ValueError, TypeError):
        pass
    if _cal["at"] and _cal["vec"] is None:
        # A zero stored by the old angle-domain calibration. It cannot be
        # converted honestly - the angles it holds were taken after the axis
        # mapping was applied, so the sensor-frame vector behind them is not
        # recoverable - and applying it would reintroduce exactly the error
        # this was changed to remove. Ask for one more press of Calibrate.
        _cal_stale = True
        _cal_reason = "calibrate again: the stored zero predates this firmware"
        print("level:", _cal_reason)
    elif _cal["at"] and _cal["chip"] != CHIP:
        # a zero taken with another sensor, in its own frame and mounting: it
        # says nothing about this one
        _cal_stale = True
        _cal_reason = "calibrate again: the levelling sensor has been changed"
        print("level:", _cal_reason)
    _set_rot()
    return _cal


def _save_cal():
    tmp = CAL_FILE + ".tmp"
    try:
        import os
        with open(tmp, "w") as f:
            json.dump(_cal, f)
        try:
            os.remove(CAL_FILE)
        except OSError:
            pass
        os.rename(tmp, CAL_FILE)
    except OSError as e:
        print("level: could not save calibration:", e)


# --- sensor ------------------------------------------------------------------
def find(devices):
    """Which sensor is on the bus, from an I2C scan: its address, or None.
    The MPU-6050 if both are there."""
    global ADDR, CHIP
    if 0x68 in devices:
        ADDR, CHIP = 0x68, "mpu6050"
        return ADDR
    for a in _LIS_ADDRS:
        if a in devices:
            ADDR, CHIP = a, "lis3dh"
            return ADDR
    return None


def _init_lis3dh():
    who = _i2c.readfrom_mem(ADDR, 0x0F, 1)[0]
    if who != _LIS_WHO:
        return "unexpected WHO_AM_I 0x%02x (not an LIS3DH)" % who
    # 100 Hz, all three axes, normal power. The part filters to half its
    # output rate itself, so there is no separate low-pass to set as on the
    # MPU-6050; 100 Hz keeps the fridge and the pump well above what is kept.
    _i2c.writeto_mem(ADDR, _LIS_CTRL1, b"\x57")
    # block data update (never half of one sample and half of the next),
    # +/-2g, high resolution (12 bits)
    _i2c.writeto_mem(ADDR, _LIS_CTRL4, b"\x88")
    # its temperature sensor, through the third ADC channel
    _i2c.writeto_mem(ADDR, _LIS_TEMP_CFG, b"\xC0")
    time.sleep_ms(50)
    return ""


def init(i2c):
    """Wake the sensor, band-limit it, and confirm it is really there."""
    global _i2c, _ok, _err
    _i2c = i2c
    try:
        if CHIP == "lis3dh":
            e = _init_lis3dh()
            _ok, _err = not e, e
            if _ok:
                load_cal()
            return _ok
        who = _i2c.readfrom_mem(ADDR, _WHO_AM_I, 1)[0]
        if who != 0x68:
            _ok = False
            _err = "unexpected WHO_AM_I 0x%02x" % who
            return False
        _i2c.writeto_mem(ADDR, _PWR_MGMT_1, b"\x00")     # out of sleep
        _i2c.writeto_mem(ADDR, _ACCEL_CONFIG, b"\x00")   # +/-2g
        # DLPF_CFG. The part defaults to 0, which is no filter at all: 260 Hz of
        # accelerometer bandwidth sampled here at 250 Hz, so half the spectrum
        # folds back on itself. 6 is the narrowest setting, 5 Hz, which is still
        # ten times faster than a parked van changes attitude.
        dlpf = int(getattr(cfg, "LEVEL_DLPF", 6))
        if dlpf < 0 or dlpf > 6:
            dlpf = 6
        _i2c.writeto_mem(ADDR, _CONFIG, bytes([dlpf]))
        # The 5 Hz filter has about 19 ms of group delay; let it settle before
        # anything reads a number out of it.
        time.sleep_ms(200)
        _ok = True
        _err = ""
        load_cal()
        return True
    except Exception as e:
        _ok = False
        _err = "%s: %s" % (type(e).__name__, e)
        return False


def _read_raw():
    """One sample as (x, y, z) in g plus the die temperature in C, or None."""
    if CHIP == "lis3dh":
        try:
            _i2c.readfrom_mem_into(ADDR, _LIS_OUT, _lisbuf)
            _i2c.readfrom_mem_into(ADDR, _LIS_ADC3, _listmp)
        except Exception:
            return None
        # The LIS3DH's temperature is relative - a change from an unknown
        # zero, a degree a count - so this is only a rough figure; its use
        # here is the drift log, which wants changes, not the true value.
        return (_s16(_lisbuf[1], _lisbuf[0]) / _LIS_LSB_PER_G,
                _s16(_lisbuf[3], _lisbuf[2]) / _LIS_LSB_PER_G,
                _s16(_lisbuf[5], _lisbuf[4]) / _LIS_LSB_PER_G,
                25.0 + _s16(_listmp[1], _listmp[0]) / 256.0)
    try:
        _i2c.readfrom_mem_into(ADDR, _ACCEL_XOUT_H, _buf)
    except Exception:
        return None
    # 340 LSB/degC about a 36.53 degC intercept, per the product specification.
    # This is the die, not the cabin - it reads a few degrees above ambient -
    # but drift tracks the die, which is what it is wanted for.
    t = _s16(_buf[6], _buf[7]) / 340.0 + 36.53
    return (_s16(_buf[0], _buf[1]) / LSB_PER_G,
            _s16(_buf[2], _buf[3]) / LSB_PER_G,
            _s16(_buf[4], _buf[5]) / LSB_PER_G,
            t)


def _averaged():
    """Mean of SAMPLES reads as (x, y, z, temp). Also leaves the widest spread
    of any one axis across those reads in _spread, which is what says whether
    the van was still while they were taken."""
    global _spread
    sx = sy = sz = st = 0.0
    lo = [9.0, 9.0, 9.0]
    hi = [-9.0, -9.0, -9.0]
    n = 0
    for _ in range(SAMPLES):
        s = _read_raw()
        if s:
            sx += s[0]
            sy += s[1]
            sz += s[2]
            st += s[3]
            for i in range(3):
                if s[i] < lo[i]:
                    lo[i] = s[i]
                if s[i] > hi[i]:
                    hi[i] = s[i]
            n += 1
        time.sleep_ms(4)
    if not n:
        _spread = 9.0
        return None
    _spread = max(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2])
    return sx / n, sy / n, sz / n, st / n


def _plausible(mag):
    """Is a total this size gravity at all? Only a sanity check: cheap
    MPU-6050s commonly read 5-10% high or low at rest (their zero-g offset and
    gain are loosely trimmed), so a magnitude near but not at 1 g is the
    sensor, not movement. Movement is judged by how much readings vary."""
    return 0.8 < mag < 1.2


def _angles(x, y, z):
    """Roll and pitch in degrees from a gravity vector already in the van frame.

    atan2 against the magnitude of the other two axes, so it stays correct
    through steep angles instead of breaking down near 90 degrees.

    The orientation fix is applied here, not at the call sites, so read() and
    calibrate() cannot end up working in different frames. Note that it is
    applied after the mounting rotation: the rotation is physical and the swap
    and inversions are presentation, which is why changing them no longer
    invalidates a stored zero.
    """
    roll = math.degrees(math.atan2(y, math.sqrt(x * x + z * z)))
    pitch = math.degrees(math.atan2(-x, math.sqrt(y * y + z * z)))
    if getattr(cfg, "LEVEL_SWAP_XY", False):
        roll, pitch = pitch, roll
    if getattr(cfg, "LEVEL_INVERT_ROLL", False):
        roll = -roll
    if getattr(cfg, "LEVEL_INVERT_PITCH", False):
        pitch = -pitch
    return roll, pitch


def invalidate():
    """Drop the cached reading, so the next call measures again."""
    global _last
    _last = None


def age_ms():
    """How old the last reading is, or None if there has never been one.

    The sampling loop feeds the cache and the endpoint serves it, so a loop that
    died would leave a page showing a perfectly plausible reading that never
    changes again. Publishing the age lets that be caught instead of stared at.
    """
    if _last is None:
        return None
    return time.ticks_diff(time.ticks_ms(), _last_ms)


def orientation_changed():
    """Re-check the stored zero after the axis mapping is changed.

    With the zero stored as a vector in the sensor's own axes this no longer
    invalidates anything - the swap and inversions relabel the output, they do
    not move the sensor - so this now only drops the cached reading, which was
    computed under the old labels. A pre-vector calibration is still reported as
    stale, because it was stored in the old frame and genuinely is.
    """
    global _last
    _last = None
    return _cal_stale


def _cache(st):
    global _last, _last_ms
    _last = st
    _last_ms = time.ticks_ms()
    return st


def read(max_age_ms=0):
    """Levelling state, already corrected for the stored calibration.

    max_age_ms: accept a cached reading this recent instead of measuring again.
    Zero always measures. Callers that are driving a live display pass a small
    value; anything that just wants the current attitude alongside other data
    can afford a second-old figure - the van does not move quickly while it is
    parked, which is the only time this is looked at.
    """
    if (_last is not None and max_age_ms > 0
            and time.ticks_diff(time.ticks_ms(), _last_ms) < max_age_ms):
        return _last
    if not _ok:
        return _cache({"ok": False, "error": _err or "not initialised",
                       "calibrated": False})
    g = _averaged()
    if not g:
        return _cache({"ok": False, "error": "no reading from the sensor",
                       "calibrated": False})
    x, y, z, temp = g
    # The uncorrected attitude, kept for the diagnostics panel.
    raw_roll, raw_pitch = _angles(x, y, z)
    # A stale zero is not applied: better to show the raw attitude and say so
    # than to correct with something measured against a different frame.
    if _cal["at"] and not _cal_stale:
        rx, ry, rz = _apply_rot(x, y, z)
    else:
        rx, ry, rz = x, y, z
    r, p = _angles(rx, ry, rz)
    # Steady: the reads agreed and the total is plausibly gravity. The total is
    # NOT compared tightly with 1 g - see _plausible().
    mag = math.sqrt(x * x + y * y + z * z)
    return _cache({
        "ok": True,
        "roll": round(r, 2),          # + = right side up
        "pitch": round(p, 2),         # + = nose up
        "off_by": round(math.sqrt(r * r + p * p), 2),
        "g": round(mag, 3),
        "steady": _plausible(mag) and _spread < STILL_SPREAD_G,
        "temp_c": round(temp, 1),
        "calibrated": bool(_cal["at"]) and not _cal_stale,
        "calibrated_at": _cal["at"],
        "cal_stale": _cal_stale,
        "cal_reason": _cal_reason,
        "raw": {"roll": round(raw_roll, 2), "pitch": round(raw_pitch, 2)},
        # The bare axes, so the mounting orientation can be worked out from a
        # known tilt rather than guessed at.
        "axes": {"x": round(x, 3), "y": round(y, 3), "z": round(z, 3)},
        "orient": _orient(),
        "logging": bool(getattr(cfg, "LEVEL_LOG_ENABLED", False)),
        "log_rows": _log_rows,
    })


def calibrate():
    """Store the current attitude as level. The van must be on flat ground."""
    global _cal, _cal_stale, _cal_reason, _last
    if not _ok:
        return {"ok": False, "error": _err or "sensor not available"}
    # Several averages over about a second and a half, which must agree with
    # each other: calibrating while the van rocks would bake the movement into
    # the zero. Their mean is the zero, which also quietens it.
    sx = sy = sz = 0.0
    lo = [9.0, 9.0, 9.0]
    hi = [-9.0, -9.0, -9.0]
    worst = 0.0
    for b in range(CAL_BLOCKS):
        if b:
            time.sleep_ms(CAL_GAP_MS)
        g = _averaged()
        if not g:
            return {"ok": False, "error": "no reading from the sensor"}
        worst = max(worst, _spread)
        for i in range(3):
            lo[i] = min(lo[i], g[i])
            hi[i] = max(hi[i], g[i])
        sx += g[0]
        sy += g[1]
        sz += g[2]
    drift = max(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2])
    if worst >= STILL_SPREAD_G or drift >= STILL_SPREAD_G:
        return {"ok": False,
                "error": "the van is not still (readings vary by %.3f g) - "
                         "try again once it settles" % max(worst, drift)}
    x, y, z = sx / CAL_BLOCKS, sy / CAL_BLOCKS, sz / CAL_BLOCKS
    mag = math.sqrt(x * x + y * y + z * z)
    if not _plausible(mag):
        return {"ok": False,
                "error": "the sensor reads %.2f g at rest, too far from 1 g to "
                         "trust - check its wiring and mounting" % mag}
    roll, pitch = _angles(x, y, z)
    _cal = {"vec": (x / mag, y / mag, z / mag),
            "roll": roll, "pitch": pitch,
            "at": int(time.time()), "orient": _orient(), "chip": CHIP}
    _cal_stale = False
    _cal_reason = ""
    _set_rot()
    _last = None          # the stored zero changed: do not serve the old figure
    _save_cal()
    print("level: calibrated roll=%.2f pitch=%.2f" % (roll, pitch))
    return {"ok": True, "roll": round(roll, 2), "pitch": round(pitch, 2),
            "at": _cal["at"]}


def clear_cal():
    global _cal, _cal_stale, _cal_reason, _last, _rot
    _cal = {"vec": None, "roll": 0.0, "pitch": 0.0, "at": 0, "orient": "", "chip": CHIP}
    _cal_stale = False
    _cal_reason = ""
    _rot = None
    _last = None
    _save_cal()
    return {"ok": True}


# --- drift log ---------------------------------------------------------------
# Off by default. The point of it is to answer one question - does the error
# track temperature? - and that only needs a couple of days of data, after
# which it should be switched off again rather than left writing to flash.
def _log_count():
    """Count what is already there, once, so a reboot does not restart the trim
    counter and let the file grow without bound."""
    global _log_rows, _log_counted
    if _log_counted:
        return
    _log_counted = True
    try:
        n = 0
        with open(LOG_FILE) as f:
            for _l in f:
                n += 1
        _log_rows = n
    except OSError:
        _log_rows = 0


def _log_trim(keep):
    global _log_rows
    try:
        import os
        with open(LOG_FILE) as f:
            rows = [l for l in f if l.strip()][-keep:]
        with open(LOG_FILE + ".tmp", "w") as f:
            for l in rows:
                f.write(l)
        try:
            os.remove(LOG_FILE)
        except OSError:
            pass
        os.rename(LOG_FILE + ".tmp", LOG_FILE)
        _log_rows = len(rows)
    except (OSError, MemoryError) as e:
        print("level log trim failed:", e)


def maybe_log(st):
    """Append a row if one is due. Called from the sampling loop.

    Only steady readings are recorded: a row taken while somebody was climbing
    about inside says nothing about drift and would sit in the data looking like
    a real excursion.
    """
    global _log_last_t, _log_rows
    if not getattr(cfg, "LEVEL_LOG_ENABLED", False):
        return
    # Counted here rather than at the first write, so the row count the page
    # shows is right from the moment logging is switched on instead of reading
    # zero over a file that already has days of data in it.
    _log_count()
    if not st or not st.get("ok") or not st.get("steady"):
        return
    t = int(time.time())
    # Before the clock is set, time.time() is near zero and every row would
    # carry a meaningless timestamp. Wait for it.
    if t < 1600000000:
        return
    every = max(30, int(getattr(cfg, "LEVEL_LOG_PERIOD_S", 300)))
    if _log_last_t and t - _log_last_t < every:
        return
    _log_last_t = t
    a = st.get("axes") or {}
    try:
        with open(LOG_FILE, "a") as f:
            f.write("%d,%.1f,%.3f,%.3f,%.3f,%.3f,%.4f,%.4f,%.4f\n" % (
                t, st.get("temp_c", 0.0),
                st.get("roll", 0.0), st.get("pitch", 0.0),
                (st.get("raw") or {}).get("roll", 0.0),
                (st.get("raw") or {}).get("pitch", 0.0),
                a.get("x", 0.0), a.get("y", 0.0), a.get("z", 0.0)))
        _log_rows += 1
        keep = max(200, int(getattr(cfg, "LEVEL_LOG_MAX_ROWS", 3000)))
        if _log_rows > keep + 200:
            _log_trim(keep)
    except OSError as e:
        print("level log write failed:", e)


def clear_log():
    global _log_rows, _log_last_t
    try:
        import os
        os.remove(LOG_FILE)
    except OSError:
        pass
    _log_rows = 0
    _log_last_t = 0
    return {"ok": True}


# ---- the trip recorder ---------------------------------------------------------
# The last 3000 readings - about fifteen minutes - kept in memory, for the G-force view's
# drives: a phone or laptop watching over Wi-Fi loses the hub as soon as the
# van leaves the network, so the hub keeps the record itself and it is
# downloaded afterwards (GET /api/level/trip). Every reading the level loop
# takes goes in (about three a second), moving or still; about 18 KB in all.
# Lost on a restart.

from array import array

TRIP_N = 3000                       # about fifteen minutes at the level loop's pace
_trip_t = array("i", bytes(4 * TRIP_N))   # ticks_ms
_trip_r = array("h", bytes(2 * TRIP_N))   # roll, hundredths of a degree
_trip_p = array("h", bytes(2 * TRIP_N))   # pitch, hundredths of a degree
_trip_m = array("h", bytes(2 * TRIP_N))   # total acceleration, thousandths of a g
_trip_i = 0                         # where the next one goes
_trip_n = 0                         # how many there are


def trip_add(st):
    """Record one reading from the level loop."""
    global _trip_i, _trip_n
    if not st or not st.get("ok"):
        return
    ax = st.get("axes") or {}
    mag = math.sqrt((ax.get("x") or 0.0) ** 2 + (ax.get("y") or 0.0) ** 2 + (ax.get("z") or 0.0) ** 2)
    i = _trip_i
    _trip_t[i] = time.ticks_ms()
    _trip_r[i] = max(-32000, min(32000, int(round((st.get("roll") or 0.0) * 100))))
    _trip_p[i] = max(-32000, min(32000, int(round((st.get("pitch") or 0.0) * 100))))
    _trip_m[i] = max(0, min(32000, int(round(mag * 1000))))
    _trip_i = (i + 1) % TRIP_N
    if _trip_n < TRIP_N:
        _trip_n += 1


TRIP_HEADER = "t,ms,roll,pitch,g_total\n"


def trip_chunks(rows=100):
    """The record as CSV text, oldest first, a hundred rows at a time: t is the
    clock time (seconds), ms the milliseconds since the first row, roll and
    pitch in degrees (calibrated, as the Level page shows them) and g_total the
    size of the measured acceleration."""
    n = _trip_n
    if not n:
        return
    first = (_trip_i - n) % TRIP_N
    # Clock times in whole milliseconds, as integers: MicroPython's floats here
    # are single precision, which holds a time like 1790946240 only to the
    # nearest 128 s - every row of a drive came out with the same time.
    now_t, now_ms = time.ticks_ms(), time.time() * 1000
    t0 = _trip_t[first]
    out = []
    for k in range(n):
        i = (first + k) % TRIP_N
        t = _trip_t[i]
        at = now_ms - time.ticks_diff(now_t, t)
        out.append("%d.%01d,%d,%.2f,%.2f,%.3f\n" % (
            at // 1000, (at % 1000) // 100, time.ticks_diff(t, t0),
            _trip_r[i] / 100, _trip_p[i] / 100, _trip_m[i] / 1000))
        if len(out) >= rows:
            yield "".join(out)
            out = []
    if out:
        yield "".join(out)
