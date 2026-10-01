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
#define BATTERY_DIVIDER 3.0f       // 1:3 resistor divider (Waveshare BAT_Driver)
#define BATTERY_ADC_TRIM 0.992357f  // Waveshare's measured correction

// Audio: ES8311 speaker codec + ES7210 mic ADC on I2C, shared I2S bus, NS4150-style speaker amp.
#define HAS_AUDIO 1
#define PIN_I2C_SDA 11
#define PIN_I2C_SCL 10
#define PIN_I2S_MCLK 12
#define PIN_I2S_BCLK 13
#define PIN_I2S_LRCK 14
#define PIN_I2S_DOUT 16  // ESP32 -> ES8311 (speaker)
#define PIN_I2S_DIN 15   // ES7210 -> ESP32 (microphones)
#define PIN_SPEAKER_AMP 9
#define ES8311_I2C_ADDR 0x18
#define ES7210_I2C_ADDR 0x40

#else
#error "Define a board, e.g. -DBOARD_DUALEYE_128"
#endif
