# Van display settings. Copy to config.py and fill in.
#
# config.py stays as plain source on the device, like the hub's, so it can be
# edited in place (mpremote edit config.py) without rebuilding anything.

# Networks to join, tried in order. The same ones the hub uses.
WIFI_NETWORKS = [
    ("WiFi", "password"),
    ("Camperlux", "password"),       # the hub's own hotspot, as a fallback
]

# Where to find the hub if it does not answer the discovery broadcast (see
# hub.py). The display normally finds it on its own, whatever address the
# router gave it; this is the fallback - the hub's own hotspot address.
HUB_HOSTS = ["192.168.4.1"]

# The hub's API token, if one is set on its Settings page. Needed for the
# heater buttons only; everything else is read without one.
API_TOKEN = ""

# 1 = landscape, 3 = landscape turned the other way up.
ROTATION = 1

# Pages, in tab order: home, drive, power, heater, level, battery, lights. The
# first is shown at start-up.
PAGES = ["home", "power", "heater", "level", "switches", "drive"]

# The van, for the Level page's wheel heights: VW Crafter MWB (2017 on).
# Wheelbase 3640 mm; track 1770 front / 1784 rear, averaged. Measure your own
# (centre of one tyre to the centre of the other) if you want it exact.
VAN_WHEELBASE_MM = 3640
VAN_TRACK_MM = 1777

# The Drive page: things to tick off before setting off. The hook-up, heater,
# alarms and battery are checked by the hub on their own. The page comes up
# when the engine starts with anything left, and the ticks clear once the van
# has been parked CHECKLIST_RESET_MIN minutes.
CHECKLIST = ["Roof vents shut", "Gas off", "Cupboards latched", "Windows shut",
             "Step and awning in", "Ramps removed"]
CHECKLIST_RESET_MIN = 30

# Night mode: red on black, from sunset to sunrise at the van's location (from
# the hub). "auto", "on" or "off" - also changed on the screen.
NIGHT_MODE = "auto"
NIGHT_BRIGHTNESS = 0.3      # the screen is never brighter than this at night

# How often to ask the hub for data. Every poll is work for the hub, which is
# short of memory, so the display backs off while nobody is looking at it.
POLL_S = 5                  # screen on
POLL_DIM_S = 30             # screen dimmed
LEVEL_POLL_MS = 1000        # while the Level page is open

# Status light: the RGB LED on the back. Gold = all well, amber = battery below
# LED_LOW_SOC %, white blink = hub not reachable, red-and-blue beacon = an
# alarm nobody has cleared (always full brightness). Otherwise dims with the
# screen at night and goes off with it.
LED = True
LED_BRIGHTNESS = 0.25       # 0-1; it is bright, and faces the wall
LED_LOW_SOC = 20

# Screensaver: just the Camperlux logo, after this long untouched. A tap
# wakes it onto the Home page; like the dimmed screen, that tap does nothing
# else. An alarm always takes over the screen.
SAVER_AFTER_S = 60

# Backlight. The screen dims after a while untouched and a tap wakes it (the
# waking tap does nothing else, so a sleepy prod cannot start the heater).
BRIGHTNESS = 1.0
DIM_BRIGHTNESS = 0.06
DIM_AFTER_S = 120
OFF_AFTER_S = 0             # 0 = never switch off entirely
WAKE_ON_ALERT = True        # an alert from the hub wakes the screen
HOME_WHEN_IDLE = True       # go back to the Home page (the clock) when dimming

# Brightness and sound are set on the screen itself (tap the clock) and
# remembered on the display; these are only the starting values.
SOUND = True                # click when the screen is touched
SOUND_VOLUME = 60           # clicks, 0-100, relative to the alarm
ALARM_VOLUME = 90           # 0-100. The alarm always sounds when the hub's alert
                            # sound is on, whatever the click switch says.
ALERT_POLL_MS = 2000        # how often to ask the hub for alerts, dimmed or not

# Weather comes from the hub's cached forecast.
WEATHER_POLL_S = 600

# Clock. The time comes from the hub. UK: offset 0, with EU summer time.
UTC_OFFSET_MIN = 0
DST_EU = True
