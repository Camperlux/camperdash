/*
 * Copyright (c) 2024 Raspberry Pi (Trading) Ltd.
 *
 * SPDX-License-Identifier: BSD-3-Clause
 */

// pico-sdk board header for the Waveshare RP2350-Relay-6CH-W: the Pico 2 W's
// header (pico2_w.h) with this board's differences, taken from Waveshare's own
// header in their demo package (C/boards/waveshare_rp2350_relay_6ch_w.h):
//  - an RP2350B (48 GPIOs), not the Pico 2 W's RP2350A
//  - 16 MB of flash, not 4
//  - the CYW43439 on GPIO 32-35, not 23-25 and 29; VSYS sensed on GPIO 43
// Waveshare's header leaves out PICO_CYW43_SUPPORTED, without which pico-sdk
// does not build the WiFi driver, and the SMPS pin; both are as the Pico 2 W.

#ifndef _BOARDS_WAVESHARE_RP2350_RELAY_6CH_W_H
#define _BOARDS_WAVESHARE_RP2350_RELAY_6CH_W_H

pico_board_cmake_set(PICO_PLATFORM, rp2350)
pico_board_cmake_set(PICO_CYW43_SUPPORTED, 1)

#define WAVESHARE_RP2350_RELAY_6CH_W

// --- RP2350 VARIANT ---
#define PICO_RP2350A 0

// --- UART ---
#ifndef PICO_DEFAULT_UART
#define PICO_DEFAULT_UART 0
#endif
#ifndef PICO_DEFAULT_UART_TX_PIN
#define PICO_DEFAULT_UART_TX_PIN 0
#endif
#ifndef PICO_DEFAULT_UART_RX_PIN
#define PICO_DEFAULT_UART_RX_PIN 1
#endif

// --- I2C ---
#ifndef PICO_DEFAULT_I2C
#define PICO_DEFAULT_I2C 0
#endif
#ifndef PICO_DEFAULT_I2C_SDA_PIN
#define PICO_DEFAULT_I2C_SDA_PIN 4
#endif
#ifndef PICO_DEFAULT_I2C_SCL_PIN
#define PICO_DEFAULT_I2C_SCL_PIN 5
#endif

// --- SPI ---
#ifndef PICO_DEFAULT_SPI
#define PICO_DEFAULT_SPI 0
#endif
#ifndef PICO_DEFAULT_SPI_SCK_PIN
#define PICO_DEFAULT_SPI_SCK_PIN 18
#endif
#ifndef PICO_DEFAULT_SPI_TX_PIN
#define PICO_DEFAULT_SPI_TX_PIN 19
#endif
#ifndef PICO_DEFAULT_SPI_RX_PIN
#define PICO_DEFAULT_SPI_RX_PIN 16
#endif
#ifndef PICO_DEFAULT_SPI_CSN_PIN
#define PICO_DEFAULT_SPI_CSN_PIN 17
#endif

// --- FLASH ---
#define PICO_BOOT_STAGE2_CHOOSE_W25Q080 1

#ifndef PICO_FLASH_SPI_CLKDIV
#define PICO_FLASH_SPI_CLKDIV 2
#endif

pico_board_cmake_set_default(PICO_FLASH_SIZE_BYTES, (16 * 1024 * 1024))
#ifndef PICO_FLASH_SIZE_BYTES
#define PICO_FLASH_SIZE_BYTES (16 * 1024 * 1024)
#endif

// --- CYW43 ---
#ifndef CYW43_WL_GPIO_COUNT
#define CYW43_WL_GPIO_COUNT 3
#endif

#ifndef CYW43_WL_GPIO_LED_PIN
#define CYW43_WL_GPIO_LED_PIN 0
#endif

// If CYW43_WL_GPIO_SMPS_PIN is defined, the CYW43 GPIO is used to control the SMPS
#ifndef CYW43_WL_GPIO_SMPS_PIN
#define CYW43_WL_GPIO_SMPS_PIN 1
#endif

// If CYW43_WL_GPIO_VBUS_PIN is defined then a CYW43 GPIO has to be used to read VBUS.
#ifndef CYW43_WL_GPIO_VBUS_PIN
#define CYW43_WL_GPIO_VBUS_PIN 2
#endif

// If CYW43_USES_VSYS_PIN is defined then CYW43 uses the VSYS GPIO for other purposes.
#ifndef CYW43_USES_VSYS_PIN
#define CYW43_USES_VSYS_PIN 1
#endif

#ifndef PICO_VSYS_PIN
#define PICO_VSYS_PIN 43
#endif

pico_board_cmake_set_default(PICO_RP2350_A2_SUPPORTED, 1)
#ifndef PICO_RP2350_A2_SUPPORTED
#define PICO_RP2350_A2_SUPPORTED 1
#endif

#ifndef CYW43_PIN_WL_DYNAMIC
#define CYW43_PIN_WL_DYNAMIC 0
#endif

#ifndef CYW43_DEFAULT_PIN_WL_REG_ON
#define CYW43_DEFAULT_PIN_WL_REG_ON 32u
#endif
#ifndef CYW43_DEFAULT_PIN_WL_DATA_OUT
#define CYW43_DEFAULT_PIN_WL_DATA_OUT 33u
#endif
#ifndef CYW43_DEFAULT_PIN_WL_DATA_IN
#define CYW43_DEFAULT_PIN_WL_DATA_IN 33u
#endif
#ifndef CYW43_DEFAULT_PIN_WL_HOST_WAKE
#define CYW43_DEFAULT_PIN_WL_HOST_WAKE 33u
#endif
#ifndef CYW43_DEFAULT_PIN_WL_CLOCK
#define CYW43_DEFAULT_PIN_WL_CLOCK 35u
#endif
#ifndef CYW43_DEFAULT_PIN_WL_CS
#define CYW43_DEFAULT_PIN_WL_CS 34u
#endif

#endif
