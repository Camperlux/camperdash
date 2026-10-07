"""The hub and the display finding each other, in the situations a van meets.

Runs the hub's real Wi-Fi code (pico/main.py) and the display's real reconnect
code (display/main.py, display/hub.py) together, in virtual time, against a
model of the radios and of what is in range of what (tests/wifisim.py). Each
scenario runs twice: somewhere quiet, with no other Wi-Fi about, and somewhere
busy, with neighbours' networks on the air - the hub's scan sees the
difference, and once behaved very differently in the two.

What these caught (Oct 2026): back on its home network after a drive, the
hub had no route out to the internet - the hotspot, started while away, had
taken it - so every scenario now checks the hub has the internet whenever it
is on a network. And before that: the hub took its hotspot down to try joining
every 15 s - every network blind wherever its scan found nothing at all, and
any network it could hear but not join - so the hotspot was off for up to two
thirds of the time, phones were turned away, and the display, retrying on its
own rhythm, could miss it indefinitely.

The model's assumptions: a scan takes the radio off the hotspot's channel for
2.2 s; a join needs the hotspot there for its first 3 s. Real radios retry
within a join, so real recoveries should be no slower than these.

Run:  python tests/test_wifi_scenarios.py        (-v prints each scenario's events)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wifisim as S                                              # noqa: E402

VERBOSE = "-v" in sys.argv
HOME_ON = [{"ssid": "WiFi", "password": "homepw", "on": True},
           {"ssid": "PhoneHotspot", "password": "phonepw", "on": True}]
HOME_OFF = [{"ssid": "WiFi", "password": "homepw", "on": False},
            {"ssid": "PhoneHotspot", "password": "phonepw", "on": True}]


class Home(S.World):
    """Everything at home; the hub's home network switched off in its Settings."""
    def disp_sees(self, s, t): return s == "WiFi"
    def hub_sees(self, s, t): return s == "WiFi"


class DriveOff(S.World):
    """Both at home and joined; at 60 s both leave home's range together."""
    def disp_sees(self, s, t): return s == "WiFi" and t < 60000
    def hub_sees(self, s, t): return s == "WiFi" and t < 60000


class Edge(S.World):
    """Parked at the edge of home's range: the hub hears home but can't join it."""
    def hub_sees(self, s, t): return s == "WiFi"
    def hub_join_ok(self, s, t): return False


class EdgeDisplayHome(Edge):
    """As Edge, but the display has a good home signal (where the hub is not)."""
    def disp_sees(self, s, t): return s == "WiFi"


class Carried(S.World):
    """Away from home; the display out of the hotspot's range 60-360 s."""
    def disp_near_hub(self, t): return not (60000 <= t < 360000)


class Indoors(S.World):
    """Van in the street; the display taken indoors onto home Wi-Fi (out of
    the hotspot's range) 60-360 s, then back out to the van."""
    def disp_near_hub(self, t): return not (60000 <= t < 360000)
    def disp_sees(self, s, t): return s == "WiFi" and 60000 <= t < 400000


class Arrive(S.World):
    """Both away, on the hotspot; at 60 s home comes into range for both."""
    def disp_sees(self, s, t): return s == "WiFi" and t >= 60000
    def hub_sees(self, s, t): return s == "WiFi" and t >= 60000


class RoundTrip(S.World):
    """This morning's drive (5 Oct 2026): at home and joined; away up the road
    60-360 s; back on the drive."""
    def disp_sees(self, s, t): return s == "WiFi" and not (60000 <= t < 360000)
    def hub_sees(self, s, t): return s == "WiFi" and not (60000 <= t < 360000)


class PhoneHotspot(S.World):
    """Away, with the phone's hotspot on: the hub joins it and keeps its own
    hotspot alongside (no clash) - the hotspot starting after the join."""
    def hub_sees(self, s, t): return s == "PhoneHotspot"


class Away(S.World):
    """Away for a while, nothing known on the air."""


# name, world, the hub's networks, where the display starts, run for (s),
# event at (s), phone from (s), and the limits: back within (s), hotspot on at least
SCENARIOS = [
    ("home network switched off on the hub", Home, HOME_OFF, "WiFi", 400, 0, 10, 90, 0.99),
    ("driving off", DriveOff, HOME_ON, "WiFi", 600, 60, 75, 60, 0.95),
    ("at the edge of home Wi-Fi", Edge, HOME_ON, "Camperlux", 900, 0, 30, 60, 0.90),
    ("edge of home Wi-Fi, display on home", EdgeDisplayHome, HOME_ON, "Camperlux", 900, 0, 30, 120, 0.90),
    # out of range, the display goes round its networks: hotspot, phone, a pause
    ("display carried out of range and back", Carried, HOME_ON, "Camperlux", 900, 360, 400, 60, 0.99),
    # indoors it settles back on home Wi-Fi after HUB_AVOID_S, so once back at
    # the van it first has to find the hub missing there (HUB_MISSING_S)
    ("display indoors on home Wi-Fi, then back", Indoors, HOME_ON, "Camperlux", 900, 360, None, 150, 0.99),
    ("arriving home", Arrive, HOME_ON, "Camperlux", 600, 60, None, 30, 0.0),
    ("away for 40 min", Away, HOME_ON, "Camperlux", 2400, 0, 0, 30, 0.99),
    ("driving away and back", RoundTrip, HOME_ON, "WiFi", 900, 360, None, 30, 0.0),
    ("on the phone's hotspot", PhoneHotspot, HOME_ON, "Camperlux", 600, 0, 30, 60, 0.95),
]

failed = []
print("%-44s %-6s %8s %9s %8s %9s %7s" % ("scenario", "place", "back in", "talking", "hotspot", "internet", "phone"))
for name, world, nets, start, run_s, event_s, phone_s, back_max, ap_min in SCENARIOS:
    for busy in (False, True):
        w = world()
        w.busy = busy
        hub, talk, view = S.run(name, w, nets, start, run_s, phone_from_s=phone_s)
        m = S.measure(talk, event_s)
        place = "busy" if busy else "quiet"
        print("%-44s %-6s %8s %8.0f%% %7.0f%% %9s %7s" % (
            name, place, "never" if m["back_s"] is None else "%.0f s" % m["back_s"],
            m["talking"] * 100, m["hotspot_on"] * 100,
            "-" if m["internet"] is None else "%.0f%%" % (m["internet"] * 100),
            "%d/%d" % (m["phone_ok"], m["phone_tries"]) if m["phone_tries"] else "-"))
        if VERBOSE:
            S.dump(max(0, event_s - 5), event_s + 200)
        if m["back_s"] is None or m["back_s"] > back_max:
            failed.append("%s (%s): display back in %s, limit %d s" % (name, place, m["back_s"], back_max))
        # On a network, the hub must have its route out: it once rejoined home
        # after a drive with none (the hotspot had taken it), Oct 2026.
        if m["internet"] is not None and m["internet"] < 0.95:
            failed.append("%s (%s): internet on only %.0f%% of the time on a network"
                          % (name, place, m["internet"] * 100))
        if m["hotspot_on"] < ap_min:
            failed.append("%s (%s): hotspot on %.0f%% of the time, limit %.0f%%"
                          % (name, place, m["hotspot_on"] * 100, ap_min * 100))
        # The phone's misses that are left are the hub's scans: for five minutes
        # after losing a network it looks every 15 s, the radio off the hotspot's
        # channel for each. The model counts a join that overlaps one as refused;
        # a real phone retries within the join, so this is the worst case.
        if m["phone_tries"] and m["phone_ok"] < 0.65 * m["phone_tries"]:
            failed.append("%s (%s): phone joined %d of %d times"
                          % (name, place, m["phone_ok"], m["phone_tries"]))

if failed:
    print("\nFAILED:\n  " + "\n  ".join(failed))
    sys.exit(1)
print("\nALL OK - %d scenarios, quiet and busy" % len(SCENARIOS))
