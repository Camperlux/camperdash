# G-force view: drive tests

Two short drives round the block on 2 October 2026, to check the G-force view
(the Level page's chip) reads the right way and at sensible sizes.

## How it works out the forces

An accelerometer can't tell a tilt from an acceleration. The view turns the
apparent tilt, which the levelling already measures from its flat-ground
calibration, back into the force felt:
- **Forward:** g = tan(pitch).
- **Cornering:** g = tan(roll).
- **Bumps:** the total reading's change from a running average of 1 g.

A slope therefore reads as a small steady acceleration, about 0.02 g per degree.

## The drives

The route: reverse up off the drive, downhill, a right turn, straight ahead, a
right turn, uphill, a right turn, downhill, then park on the drive.

| Drive | Recorded by | Files |
|---|---|---|
| 1 | The PC polling the hub over home Wi-Fi, 4 a second. Lost after 34 s, out of range. | `2026-10-02_drive1_pc_over_wifi.csv` (`t,host,roll,pitch,ax,ay,az,temp_c,steady`), `2026-10-02_drive1.png` |
| 2 | The hub's own trip recorder (`GET /api/level/trip`), about 3 a second. The whole loop. | `2026-10-02_drive2_hub_trip.csv` (`t,ms,roll,pitch,g_total`), `2026-10-02_drive2.png` |

The `t` column of drive 2 was written before a precision fix and is only
accurate to about 2 minutes. Use `ms`, the milliseconds since the first row.
The chart is timed from that column.

## Findings

- **Directions are right:**
  - Reversing reads backwards and nose-down.
  - The right turns read **right**: about 0.10 g for a short one, and 0.11–0.13 g for about 11 s through the last.
  - Braking onto the drive reads as braking.
- **Sizes are normal for gentle driving:** peaks of about 0.25 g accelerating, 0.11 g braking, 0.15 g cornering and 0.14 g in bumps. The view's ±0.6 g scale leaves room for harder driving.
- **Calibration holds:** back on the drive it read the same as before setting off, about 0°.
- **Two left-reading sections, at about 55 s and 65 s into drive 2:** 0.12–0.15 g for 2 s and 5 s. They aren't explained by the route as described, which has only right turns. To be confirmed: a left-hand bend, the camber, or the turn onto the drive.
- **A 10 s gap in drive 2 as the hub left Wi-Fi range.** It stops everything while it starts its hotspot: a blocking scan, then waiting up to 5 s for the hotspot. Out of range, each 15 s rejoin attempt costs about 1 s.
- **Rejoining Wi-Fi:** after this test the hub retries every 15 s for its first 5 minutes out of range. It was back on home Wi-Fi as the van came onto the drive, 50 s offline in total.
