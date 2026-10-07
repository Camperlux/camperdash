// Waveshare RP2350-Relay-6CH-W: the Camperlux hub from the RP2350 upgrade on.
// As the Pico 2 W (boards/RPI_PICO2_W), with this board's chip and WiFi pins
// coming from waveshare_rp2350_relay_6ch_w.h.
#define MICROPY_HW_BOARD_NAME                   "Waveshare RP2350-Relay-6CH-W"

// Enable networking.
#define MICROPY_PY_NETWORK 1
#define MICROPY_PY_NETWORK_HOSTNAME_DEFAULT     "Camperlux"

// CYW43 driver configuration.
#define CYW43_USE_SPI (1)
#define CYW43_LWIP (1)
#define CYW43_GPIO (1)
#define CYW43_SPI_PIO (1)

#define MICROPY_HW_PIN_EXT_COUNT    CYW43_WL_GPIO_COUNT

int mp_hal_is_pin_reserved(int n);
#define MICROPY_HW_PIN_RESERVED(i) mp_hal_is_pin_reserved(i)
