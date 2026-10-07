# cmake file for the Waveshare RP2350-Relay-6CH-W (see mpconfigboard.h)

# An RP2350B: 48 GPIOs. The relays are on 26-31 and the WiFi chip on 32-35.
set(PICO_NUM_GPIOS 48)

# pico-sdk has no header for this board: use ours, from this directory.
list(APPEND PICO_BOARD_HEADER_DIRS ${MICROPY_BOARD_DIR})
set(PICO_BOARD "waveshare_rp2350_relay_6ch_w")

set(PICO_FLASH_SIZE_BYTES 16777216)

set(MICROPY_PY_LWIP ON)
set(MICROPY_PY_NETWORK_CYW43 ON)

# Bluetooth
set(MICROPY_PY_BLUETOOTH ON)
set(MICROPY_BLUETOOTH_BTSTACK ON)
set(MICROPY_PY_BLUETOOTH_CYW43 ON)

set(MICROPY_FROZEN_MANIFEST ${MICROPY_BOARD_DIR}/manifest.py)

# 16 MB of flash: 2 MB for the firmware, the rest for files (the APK, logs)
if(NOT DEFINED MICROPY_HW_FLASH_STORAGE_BYTES)
    set(MICROPY_HW_FLASH_STORAGE_BYTES 14680064)  # 14 * 1024 * 1024
endif()
