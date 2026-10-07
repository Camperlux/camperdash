# Setting up CamperDash from scratch

This takes a blank Waveshare RP2350-Relay-6CH-W hub and a Freenove ESP32-S3
display to a working system in a van. Allow an afternoon. If you'd rather let
an AI assistant drive, give it [AI_SETUP_PROMPT.md](AI_SETUP_PROMPT.md).

## What you need

- **The hardware** in [HARDWARE.md](HARDWARE.md). The hub and USB-C cable are enough to start; the sensors and display can follow.
- **A Windows, macOS or Linux PC** with Python 3.10 or later and Git.
- **Python tools:** `pip install mpremote mpy-cross==1.29.0.post2`. The mpy-cross version must match the hub's MicroPython (1.29); `tools/build_mpy.py` checks this.
- **A GitHub account** to download the hub firmware build, or build it yourself from `firmware/`.
- **Optional:** Android Studio (or just its JDK) to build the Android app.

```bash
git clone <this repository>
cd CamperLux-Smart-Van
```

## 1. Put MicroPython on the hub

No standard MicroPython build drives this board's Wi-Fi, so use ours.

1. **Get the firmware.** In GitHub, go to **Actions → Hub firmware**, open the latest successful run, and download the artifact `hub-firmware-v1.29.0`. It contains a `.uf2` file. With the GitHub CLI:
   ```bash
   gh run download --name hub-firmware-v1.29.0
   ```
   To build it yourself, run the workflow ("Run workflow"), or follow `.github/workflows/firmware.yml` locally.
2. **Put the board in bootloader mode.** Hold **BOOT**, press and release **RESET**, then release **BOOT**. A drive called `RP2350` appears.
   - Once MicroPython is running, you can do this from the PC instead:
     ```bash
     mpremote connect <port> exec "import machine; machine.bootloader()"
     ```
3. **Copy the `.uf2` onto that drive.** The board restarts into MicroPython.
4. **Check it:**
   ```bash
   mpremote connect <port> exec "import sys, network; print(sys.version); network.WLAN(network.STA_IF).active(True); print('wifi ok')"
   ```

`<port>` is the board's serial port, e.g. `COM13` on Windows or `/dev/ttyACM0` on Linux.

## 2. Configure the hub

1. Copy `pico/config.example.py` to `pico/config.py`. **Never commit `config.py`**; it holds passwords and keys, and `.gitignore` already excludes it.
2. **Set at least:**
   - `WIFI_NETWORKS`: your home, van or phone networks. Leave it empty to start with only the hub's hotspot.
   - `AP_SSID` and `AP_PASSWORD`: the hub's own hotspot. Choose a real password.
   - `AP_IP = "192.168.4.1"`: leave it as it is. The Wi-Fi driver fixes the hotspot at this address ([HARDWARE.md](HARDWARE.md#networking)).
   - `GPS_ENABLED` and `GPS_UART = 0`, `GPS_TX_PIN = 0`, `GPS_RX_PIN = 1`, if a GPS is fitted.
   - `LEVEL_ENABLED = True`, if an accelerometer is fitted.
3. Leave everything else as it is. Most settings are changed later in the hub's Settings page and saved in `settings.json`, which overrides `config.py`.

## 3. Install the hub software

```bash
python tools/build_mpy.py                              # compiles pico/ to build/
python tools/deploy_mpy.py --port <port>               # dry run: shows what it will do
python tools/deploy_mpy.py --port <port> --go --with-config
```

- `--with-config` copies `config.py` the first time. **Leave it off on later updates**, so the hub's own `config.py` is kept.
- The tool checks the bytecode is compatible and that there is room, before writing anything.
- After the restart, watch the hub come up:
  ```bash
  mpremote connect <port>
  ```
  It prints its address. Ctrl-] leaves.

## 4. First visit to the hub

1. **Join the hub.** Join its hotspot (the `AP_SSID` you set, at http://192.168.4.1), or the same network as the hub at the address it printed.
2. **Open `/settings`.** Each step below is covered in detail, with screenshots, in [DEVICES.md](DEVICES.md):
   - **Security:** set the hub password first. The heater can be lit from these pages.
   - **Devices:** scan for and pair each Bluetooth device: BMS, Renogy BT-2, heater. The Victron needs its advertisement key from the VictronConnect app (Product info → Instant readout → Show encryption data).
   - **Weather:** **Automatic** uses the van's GPS position while it has a fix; **Fixed location** always uses the place you set. Either way, set a fixed location (where the van lives) for when there's no fix. The card says which location the forecast is for, and why.
   - **Switches:** a name and icon for each relay you've wired.
   - **Levelling:** with the van on flat ground, **Calibrate**. Set the axis swap and invert until tilting the van the right way moves the bubble the right way.
   - **Network:** add the networks the hub should join. Each has an on/off switch, to stop the hub joining one without losing its password.
3. **Check the dashboard (`/`)** shows the battery, charger and heater.

## 5. The cabin display

1. **Flash MicroPython v1.29.0 for the ESP32-S3**, the `ESP32_GENERIC_S3` build with the `SPIRAM_OCT` variant, from micropython.org, using `esptool`. Hold BOOT while connecting USB to enter its bootloader.
2. Copy `display/config.example.py` to `display/config.py`. Set `WIFI_NETWORKS` to the same networks as the hub, with the hub's hotspot last.
3. **Create the update-signing key once.** `tools/publish_display.py` creates `.ota_key` at the repository root.
   - Keep a copy somewhere safe, and never commit it.
   - A display installed with one key won't accept updates signed with another.
4. **Install over USB:**
   ```bash
   python tools/make_fonts.py                       # only the first time, or when fonts change
   python tools/deploy_display.py --port <display port> --go --with-config
   ```
5. **Pair it.** If the hub has a password, the display shows a 6-digit code. Type the code into the hub's Settings.
6. **Later updates go over Wi-Fi:**
   ```bash
   python tools/publish_display.py --hub-port <hub port> --go
   ```
   Then press **Settings → Van display → Update the display now**.

## 6. Phones and the van's screen

- **iPhone or any browser:** open the hub's address in the browser. In Safari, use Share → **Add to Home Screen** to get an app icon.
- **Android:**
  - Download the app from the hub's **Settings → Network → Download the app**. It's at `/camperlux.apk`, and `tools/build_mpy.py` puts it on the hub.
  - The app finds the hub by itself and updates itself from the hub.
- **Van head unit:**
  - Through a wireless CarPlay/Android Auto dongle, the head unit shows the phone's screen, and the browser view works on it.
  - The Android app also has an Android Auto view.

## 7. Into the van

1. **Power:** wire the hub from the leisure battery through a 2 A fuse.
2. **Relays:** wire the relay loads with a fuse per load (see [HARDWARE.md](HARDWARE.md)).
3. **Accelerometer:** mount it rigidly, then calibrate it on flat ground.
4. **Test each relay** from the Switches page, then **test each alert:** Settings → **Test alarm**.

## Troubleshooting

| Symptom | Look at |
|---|---|
| Can't find the hub | Is it on the same network? Try its hotspot at 192.168.4.1. The display and the app also search by themselves. |
| A device shows "not connected" | Bluetooth range; the device paired in Settings; on the hub's serial output, look for the scan. |
| The level reading drifts | Normal up to about 0.5° with temperature; see [level_drift/](level_drift/README.md). Recalibrate if it's more. |
| A display update doesn't arrive | Press **Update the display now** again; it's missed if sent just after either one restarts. |
| Starting again, or a forgotten hub password | Factory reset: hold BOOT for 10 s with the hub (or display) running; see [DEVICES.md](DEVICES.md#8-restarting-and-starting-again) |
| `deploy_mpy` says the ABI doesn't match | Install the `mpy-cross` that matches the hub's MicroPython version. |
