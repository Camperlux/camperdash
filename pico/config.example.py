# IntrepidVan Pico W hub - configuration TEMPLATE.
#
# Copy to config.py and fill in.  These are only the factory defaults: once
# the hub is running, everything here is set from the Settings page and saved
# to settings.json, which takes precedence.  Leave the addresses blank and
# pair the devices from that page instead.
#
# EDIT THE TWO WIFI LINES BELOW with your network, then save.
# (Password stays only on the Pico, never leaves it.)

WIFI_SSID = "your-network"
WIFI_PASSWORD = ""

# "sta" = join the WiFi above (use this now for testing).
# "ap"  = Pico makes its own hotspot (for the van later); see AP_* below.
WIFI_MODE = "sta"

AP_SSID = "Camperlux"
AP_PASSWORD = ""   # hotspot password (min 8 chars) - change it

HTTP_PORT = 80

# Heater control token. EMPTY = no token required (anyone on the WiFi can
# start/stop the heater). Set a non-empty string here to re-enable the check;
# the dashboard will then prompt for it once per device.
API_TOKEN = ""

# BLE devices (addresses from the scan)
BMS_ADDR = ""      # Fogstar JBD BMS
RENOGY_ADDR = ""   # Renogy BT-2 DC-DC charger
VICTRON_ADDR = ""  # Victron Blue Smart IP22
VICTRON_KEY = ""
HEATER_ADDR = ""   # JP/SolGP diesel heater ('Intrepid')
HEATER_POLL = False                 # DO NOT poll the heater in the background:
#   constantly connecting makes the heater flash "Bluetooth" and drop its burn.
#   The heater is only touched on demand (a command or the Refresh button).
HEATER_STALE_MS = 600000            # how long a on-demand heater reading stays shown
# Fuel when a heat command asks for "auto" (the default on every screen): this
# element setting on a live 230 V hook-up, diesel otherwise. 3 = Electric 1, the
# lower stage, least likely to trip a small campsite hook-up. 1/2 = diesel +
# electric 1/2, 4 = Electric 2. Chosen when the command is sent, never switched
# on a running heater.
HEATER_AUTO_ELECTRIC = 3

# The van display's Drive page: things to tick off before setting off (the
# hook-up, heater, alarms and battery are checked on their own). Edited on the
# Settings page; this is only the starting list. Up to 10 items, 32 characters.
CHECKLIST = ["Roof vents shut", "Gas off", "Cupboards latched", "Windows shut",
             "Step and awning in", "Ramps removed"]

# The van display's sound levels, 0-100 (100 = full design loudness, each step
# half a decibel quieter). Touch sounds 0 = off; the alarm cannot go below 30.
# Set on the Settings page.
PANEL = {"click_volume": 100, "alarm_volume": 100}

# Battery alerts. LOW_SOC is set on the Settings page; the others are limits for
# the battery-health alert.
LOW_SOC = 20                # % charge
CELL_SPREAD_MV = 150        # between cells, while between 10% and 95%
BATT_HOT_C = 50

# Local time, for "energy today" and the heater timers (the hub keeps UTC).
UTC_OFFSET_MIN = 0
DST_EU = True

# Weather: the hub fetches the forecast itself so the page still works on the
# van's hotspot, where the viewing device has no internet of its own.  Location
# is set on the Settings page (blank = no forecast until it is).
WEATHER_LAT = ""
WEATHER_LON = ""
WEATHER_PLACE = ""
WEATHER_REFRESH_MIN = 30     # how often to refresh while we have a route out
WEATHER_STALE_H = 12         # older than this and the page says so loudly

POLL_PERIOD_MS = 5000        # gap between full poll cycles
# Only the Victron needs this scan now: the BMS and DC-DC connect from their
# cached address type, so a missed advert no longer costs a reading.  Shorter
# scan = shorter poll cycle = fresher battery and DC-DC figures.
VICTRON_SCAN_MS = 2500       # how long each Victron advert scan runs
VICTRON_STALE_MS = 45000     # no advert for this long -> treat as unplugged
# A poll cycle is ~13 s, and a single missed BLE connect is normal.  Hold the
# last good reading this long before showing the panel as offline, so one
# flaky cycle no longer blanks the battery or DC-DC display.
BATTERY_STALE_MS = 60000
RENOGY_STALE_MS = 60000

# --- Board ------------------------------------------------------------------
# Peripherals some boards hold off at reset. Leave both as None on a Pico W,
# which powers its headers directly and has no reset line.
#   Heltec WiFi LoRa 32 V3: BOARD_VEXT_PIN = 36, BOARD_OLED_RESET_PIN = 21,
#                           I2C_SDA_PIN = 17, I2C_SCL_PIN = 18
BOARD_VEXT_PIN = None          # GPIO switching the peripheral power rail
BOARD_VEXT_ACTIVE_LOW = True   # most Vext rails are enabled by driving low
BOARD_OLED_RESET_PIN = None    # GPIO holding the display in reset

# --- I2C peripherals --------------------------------------------------------
# The SSD1306 display and the GY-521 (MPU-6050) levelling sensor share one bus.
# 400 kHz is comfortable over a short run; drop to 100000 if the cable to the
# display is long or the readings become unreliable.
I2C_SCL_PIN = 5
I2C_SDA_PIN = 4
I2C_FREQ = 400000

# --- OLED -------------------------------------------------------------------
# Factory defaults only: everything except ENABLED/ADDR is overridden by
# Settings -> Display and stored in settings.json.
OLED_ENABLED = True
OLED_ADDR = 0x3C
OLED_ON = True                # screen on; off blanks it without stopping the hub
OLED_BRIGHTNESS = 255         # 1-255. Full brightness is hard on the eyes at night
OLED_INVERT = False           # dark text on a lit panel
OLED_ROTATE = False           # turn the picture 180 degrees for the mounting
OLED_REFRESH_MS = 2000        # the eye does not need it faster, and this is I2C

# --- Levelling --------------------------------------------------------------
# How the GY-521 sits in the vehicle. Determine these by tilting the sensor and
# watching the Level page, not by reasoning about a resting reading. Changing
# any of them invalidates the stored zero, which must then be set again.
LEVEL_ENABLED = True
# Which layout each page uses. These belong to the user rather than to the
# code: the overview diagram is unreadable on a phone, and some people prefer
# to see the van's attitude as a vehicle rather than as a bubble.
#   OVERVIEW_VIEW: "auto" | "diagram" | "cards"
#     auto - the diagram on a wide screen, cards on a narrow one
#   LEVEL_VIEW:    "bubble" | "van"
OVERVIEW_VIEW = "auto"
LEVEL_VIEW = "bubble"
# Levelling thresholds, in degrees. The vial scale runs to LEVEL_MAX_DEG.
#   OK   - level enough to sleep on; inside this the van reads as level
#   WARN - uncomfortable to live in, and the point the vial turns red
#   MAX  - the end of the scale; set near the angle the vehicle would tip at
# These are vehicle properties, not preferences: a high-top van on soft
# suspension is not a low camper, so they are set per installation.
LEVEL_OK_DEG = 1.0
LEVEL_WARN_DEG = 20.0
LEVEL_MAX_DEG = 30.0
# How often the Level page asks for a new reading, in milliseconds. It polls a
# small dedicated endpoint rather than the whole dashboard payload, so this is
# cheap - but each measurement is still ~80 ms of blocking I2C, so there is a
# floor on how fast it is sensible to go.
LEVEL_REFRESH_MS = 500
# Accelerometer low-pass filter (the MPU-6050's DLPF_CFG, 0-6). The part powers
# up at 0, which is no filter at all: 260 Hz of bandwidth, sampled here at
# 250 Hz, so everything above 125 Hz folds back into the average as a bias. In
# a van that is the fridge compressor, the water pump and the wind. 6 is the
# narrowest setting, 5 Hz, and still an order of magnitude faster than a parked
# van changes attitude. Raise it only if the reading ever feels sluggish:
#   0 = 260 Hz   1 = 184 Hz   2 = 94 Hz   3 = 44 Hz   4 = 21 Hz   5 = 10 Hz
#   6 = 5 Hz
LEVEL_DLPF = 6

# Drift log. Off by default, and meant to be switched on for a couple of days
# to answer one question - does the levelling error follow temperature? - and
# then switched off again rather than left writing to flash.
#
# Each row is: unix time, die temperature C, roll, pitch, uncorrected roll,
# uncorrected pitch, and the three raw axes in g. Download it from
# /api/level/log. Only steady readings are recorded, so nobody moving about
# inside shows up as drift.
#
# The MPU-6050's zero point moves about 0.5 mg/degC, which is 0.03 degrees of
# apparent tilt per degree C. Over the swing a dashboard sees between a cold
# night and a sunny afternoon that is close to a degree, and no calibration
# taken at one temperature can remove it at another.
LEVEL_LOG_ENABLED = False
LEVEL_LOG_PERIOD_S = 300     # a row every five minutes is plenty for drift
LEVEL_LOG_MAX_ROWS = 3000    # ~10 days at five minutes; oldest rows are dropped

# --- External GPS (u-blox NEO-7M or similar, NMEA over UART) ----------------
# Off by default: without a receiver fitted the UART simply never answers, and
# a feature nobody has wired should not appear to be broken.
#
# Wiring to the Pico W (UART1 by default):
#   GPS VCC  -> 3V3 (pin 36)          the NEO-7M runs happily at 3.3 V
#   GPS GND  -> GND (pin 38 or any)
#   GPS TX   -> GP9  (pin 12)  = hub RX    the line that actually carries data
#   GPS RX   -> GP8  (pin 11)  = hub TX    only needed for UBX configuration
# 9600 baud, 8N1, which is the NEO-7M default.
#
# GP8/GP9 are the second set of UART1 pins. NOT GP4/GP5, which look like the
# obvious UART1 choice and are this hub's I2C bus - the display and the
# levelling sensor are on them. Sharing them does not fail loudly; it quietly
# corrupts whichever peripheral loses the argument. The pins must belong to the
# UART chosen here: GP8/GP9 and GP4/GP5 are UART1, GP0/GP1 and GP12/GP13 are
# UART0. They are not interchangeable.
GPS_ENABLED = False
GPS_UART = 1
GPS_TX_PIN = 8               # hub transmit -> receiver RX (unused today)
GPS_RX_PIN = 9               # hub receive  <- receiver TX (the important one)
GPS_BAUD = 9600
GPS_POLL_MS = 250            # how often the UART buffer is drained




LEVEL_SWAP_XY = False         # the sensor's X axis runs across the van, not along
LEVEL_INVERT_ROLL = False     # roll:  + means the RIGHT side is high
LEVEL_INVERT_PITCH = False    # pitch: + means the NOSE is high

# Publish the Renogy's raw Modbus words on /api/data. Diagnostic only - it is
# how undecoded registers get identified from a real charger. Leave off.
RENOGY_DEBUG_REGS = False

# --- Wi-Fi fallback and known networks --------------------------------------
# These have lived only in the working config until now, which meant a fresh
# checkout raised AttributeError in settings.defaults() before the hub could
# start. Every key settings.py reads must exist here.
WIFI_NETWORKS = []            # [{"ssid": ..., "password": ...}], tried in order
WIFI_AP_ALWAYS = False        # keep the hotspot up even when a network is joined
DISCOVERY_ENABLED = True      # answer the display's search; False on a hub on the bench beside the real one
WIFI_JOIN_TIMEOUT_S = 15      # how long to wait for a join before moving on
WIFI_RETRY_S = 300            # how often to retry the known networks from AP mode
# Fixed: MicroPython pins the DHCP pool when the interface comes up, so moving
# this address leaves clients unroutable. See _ap_should_run() in main.py.
AP_IP = "192.168.4.1"

# --- Alerts -----------------------------------------------------------------
ALERTS_OFF = []               # ids of warnings the user has switched off
ALERT_SOUND = True            # audible as well as on-screen

