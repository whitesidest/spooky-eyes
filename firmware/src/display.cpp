#include "display.h"

#include <Arduino.h>
#include <driver/gpio.h>
#include <driver/spi_master.h>
#include <esp_heap_caps.h>
#include <string.h>

#include "board.h"

namespace display {
namespace {

constexpr int kBuffers = 2;
constexpr size_t kStripBytes = kWidth * kStripRows * 2;
// Each strip = CASET, CASET data, RASET, RASET data, RAMWR, pixels.
constexpr int kTxPerStrip = 6;

struct Slot {
  uint16_t* pixels = nullptr;
  spi_transaction_t tx[kTxPerStrip];
  bool inFlight = false;
};

struct Panel {
  spi_device_handle_t dev = nullptr;
  Slot slots[kBuffers];
  int next = 0;
};

Panel panels[2];

// DC level travels in the transaction's user field; applied just before each transfer.
void IRAM_ATTR preTransfer(spi_transaction_t* t) { gpio_set_level((gpio_num_t)PIN_LCD_DC, (int)(intptr_t)t->user); }

void sendCmd(int p, uint8_t cmd, const uint8_t* data, size_t len) {
  spi_transaction_t t = {};
  t.length = 8;
  t.flags = SPI_TRANS_USE_TXDATA;
  t.tx_data[0] = cmd;
  t.user = (void*)0;
  spi_device_polling_transmit(panels[p].dev, &t);
  if (len) {
    spi_transaction_t d = {};
    d.length = len * 8;
    d.tx_buffer = data;
    d.user = (void*)1;
    spi_device_polling_transmit(panels[p].dev, &d);
  }
}

// Widely used GC9A01 init sequence (vendor registers + gamma).
struct InitCmd {
  uint8_t cmd;
  uint8_t len;
  uint8_t data[12];
  uint16_t delayMs;
};
const InitCmd kInit[] = {
    {0xEF, 0, {}, 0},
    {0xEB, 1, {0x14}, 0},
    {0xFE, 0, {}, 0},
    {0xEF, 0, {}, 0},
    {0xEB, 1, {0x14}, 0},
    {0x84, 1, {0x40}, 0},
    {0x85, 1, {0xFF}, 0},
    {0x86, 1, {0xFF}, 0},
    {0x87, 1, {0xFF}, 0},
    {0x88, 1, {0x0A}, 0},
    {0x89, 1, {0x21}, 0},
    {0x8A, 1, {0x00}, 0},
    {0x8B, 1, {0x80}, 0},
    {0x8C, 1, {0x01}, 0},
    {0x8D, 1, {0x01}, 0},
    {0x8E, 1, {0xFF}, 0},
    {0x8F, 1, {0xFF}, 0},
    {0xB6, 2, {0x00, 0x20}, 0},
    {0x3A, 1, {0x05}, 0},  // 16-bit colour
    {0x90, 4, {0x08, 0x08, 0x08, 0x08}, 0},
    {0xBD, 1, {0x06}, 0},
    {0xBC, 1, {0x00}, 0},
    {0xFF, 3, {0x60, 0x01, 0x04}, 0},
    {0xC3, 1, {0x13}, 0},
    {0xC4, 1, {0x13}, 0},
    {0xC9, 1, {0x22}, 0},
    {0xBE, 1, {0x11}, 0},
    {0xE1, 2, {0x10, 0x0E}, 0},
    {0xDF, 3, {0x21, 0x0C, 0x02}, 0},
    {0xF0, 6, {0x45, 0x09, 0x08, 0x08, 0x26, 0x2A}, 0},
    {0xF1, 6, {0x43, 0x70, 0x72, 0x36, 0x37, 0x6F}, 0},
    {0xF2, 6, {0x45, 0x09, 0x08, 0x08, 0x26, 0x2A}, 0},
    {0xF3, 6, {0x43, 0x70, 0x72, 0x36, 0x37, 0x6F}, 0},
    {0xED, 2, {0x1B, 0x0B}, 0},
    {0xAE, 1, {0x77}, 0},
    {0xCD, 1, {0x63}, 0},
    {0x70, 9, {0x07, 0x07, 0x04, 0x0E, 0x0F, 0x09, 0x07, 0x08, 0x03}, 0},
    {0xE8, 1, {0x34}, 0},
    {0x62, 12, {0x18, 0x0D, 0x71, 0xED, 0x70, 0x70, 0x18, 0x0F, 0x71, 0xEF, 0x70, 0x70}, 0},
    {0x63, 12, {0x18, 0x11, 0x71, 0xF1, 0x70, 0x70, 0x18, 0x13, 0x71, 0xF3, 0x70, 0x70}, 0},
    {0x64, 7, {0x28, 0x29, 0xF1, 0x01, 0xF1, 0x00, 0x07}, 0},
    {0x66, 10, {0x3C, 0x00, 0xCD, 0x67, 0x45, 0x45, 0x10, 0x00, 0x00, 0x00}, 0},
    {0x67, 10, {0x00, 0x3C, 0x00, 0x00, 0x00, 0x01, 0x54, 0x10, 0x32, 0x98}, 0},
    {0x74, 7, {0x10, 0x85, 0x80, 0x00, 0x00, 0x4E, 0x00}, 0},
    {0x98, 2, {0x3E, 0x07}, 0},
    {0x35, 0, {}, 0},
    {0x21, 0, {}, 0},    // inversion on (IPS)
    {0x11, 0, {}, 120},  // sleep out
    {0x29, 0, {}, 20},   // display on
};

void initPanel(int p) {
  for (const auto& c : kInit) {
    sendCmd(p, c.cmd, c.data, c.len);
    if (c.delayMs) delay(c.delayMs);
  }
  sendCmd(p, 0x36, &kLcdMadctl[p], 1);
}

void waitSlot(int p, Slot& s) {
  if (!s.inFlight) return;
  spi_transaction_t* done;
  for (int i = 0; i < kTxPerStrip; ++i) spi_device_get_trans_result(panels[p].dev, &done, portMAX_DELAY);
  s.inFlight = false;
}

}  // namespace

bool begin() {
  gpio_config_t io = {};
  io.mode = GPIO_MODE_OUTPUT;
  io.pin_bit_mask = 1ULL << PIN_LCD_DC;
  for (int p = 0; p < 2; ++p) io.pin_bit_mask |= 1ULL << kLcdRst[p];
  gpio_config(&io);

  spi_bus_config_t bus = {};
  bus.mosi_io_num = PIN_LCD_MOSI;
  bus.miso_io_num = -1;
  bus.sclk_io_num = PIN_LCD_SCLK;
  bus.quadwp_io_num = -1;
  bus.quadhd_io_num = -1;
  bus.max_transfer_sz = kStripBytes + 16;
  if (spi_bus_initialize(SPI2_HOST, &bus, SPI_DMA_CH_AUTO) != ESP_OK) return false;

  for (int p = 0; p < 2; ++p) {
    ledcAttach(kLcdBl[p], 20000, 8);
    ledcWrite(kLcdBl[p], 0);

    spi_device_interface_config_t dev = {};
    dev.clock_speed_hz = LCD_SPI_HZ;
    dev.mode = 0;
    dev.spics_io_num = kLcdCs[p];
    dev.queue_size = kBuffers * kTxPerStrip;
    dev.pre_cb = preTransfer;
    if (spi_bus_add_device(SPI2_HOST, &dev, &panels[p].dev) != ESP_OK) return false;

    for (auto& s : panels[p].slots) {
      s.pixels = (uint16_t*)heap_caps_malloc(kStripBytes, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
      if (!s.pixels) return false;
    }
  }

  // Hardware reset both panels together, then init.
  for (int p = 0; p < 2; ++p) gpio_set_level((gpio_num_t)kLcdRst[p], 0);
  delay(20);
  for (int p = 0; p < 2; ++p) gpio_set_level((gpio_num_t)kLcdRst[p], 1);
  delay(120);
  for (int p = 0; p < 2; ++p) initPanel(p);
  return true;
}

void setBacklight(int panel, uint8_t level) { ledcWrite(kLcdBl[panel], level); }

void setSleep(bool sleep) {
  for (int p = 0; p < 2; ++p) {
    finishFrame(p);
    sendCmd(p, sleep ? 0x28 : 0x29, nullptr, 0);
  }
}

uint16_t* beginStrip(int panel) {
  Panel& pn = panels[panel];
  Slot& s = pn.slots[pn.next];
  waitSlot(panel, s);
  return s.pixels;
}

void pushStrip(int panel, int y0) {
  Panel& pn = panels[panel];
  Slot& s = pn.slots[pn.next];
  const int y1 = y0 + kStripRows - 1;
  memset(s.tx, 0, sizeof(s.tx));
  auto cmd = [](spi_transaction_t& t, uint8_t c) {
    t.length = 8;
    t.flags = SPI_TRANS_USE_TXDATA;
    t.tx_data[0] = c;
    t.user = (void*)0;
  };
  auto data4 = [](spi_transaction_t& t, uint16_t a, uint16_t b) {
    t.length = 32;
    t.flags = SPI_TRANS_USE_TXDATA;
    t.tx_data[0] = a >> 8;
    t.tx_data[1] = a & 0xff;
    t.tx_data[2] = b >> 8;
    t.tx_data[3] = b & 0xff;
    t.user = (void*)1;
  };
  cmd(s.tx[0], 0x2A);
  data4(s.tx[1], 0, kWidth - 1);
  cmd(s.tx[2], 0x2B);
  data4(s.tx[3], y0, y1);
  cmd(s.tx[4], 0x2C);
  s.tx[5].length = kStripBytes * 8;
  s.tx[5].tx_buffer = s.pixels;
  s.tx[5].user = (void*)1;
  for (auto& t : s.tx) spi_device_queue_trans(pn.dev, &t, portMAX_DELAY);
  s.inFlight = true;
  pn.next = (pn.next + 1) % kBuffers;
}

void finishFrame(int panel) {
  for (auto& s : panels[panel].slots) waitSlot(panel, s);
}

}  // namespace display
