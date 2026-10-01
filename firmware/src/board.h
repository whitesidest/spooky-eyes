// Pin map for supported boards. Add a new block to support another board.
#pragma once

#if defined(BOARD_DUALEYE_128)
#define BOARD_MODEL "dualeye-1.28"

#define PIN_LCD_SCLK 41
#define PIN_LCD_MOSI 42
#define PIN_LCD_MISO 40
#define PIN_LCD_DC 45

// Index 0 = LCD1 (left eye as you face the board), 1 = LCD2 (right eye).
constexpr int kLcdCs[2] = {47, 38};
constexpr int kLcdRst[2] = {48, 8};
constexpr int kLcdBl[2] = {46, 39};
// MADCTL per panel: 0x08 = BGR; 0x20 = MV (swap rows/cols), 0x40 = MX, 0x80 = MY.
// The DualEye panels are mounted rotated 90 degrees, and 180 degrees from each other:
// LCD1 = MV|MX (rotate 90 CW), LCD2 = MV|MY (rotate 90 CCW).
constexpr uint8_t kLcdMadctl[2] = {0x68, 0xA8};

#define LCD_SPI_HZ 80000000
#define PIN_BATTERY_ADC 1

#else
#error "Define a board, e.g. -DBOARD_DUALEYE_128"
#endif
