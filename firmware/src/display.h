// Two GC9A01 panels on one SPI bus (shared SCLK/MOSI/DC, separate CS/RST/BL).
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace display {

constexpr int kWidth = 240;
constexpr int kHeight = 240;
constexpr int kStripRows = 24;  // rows per DMA strip; must divide kHeight

bool begin();
void setBacklight(int panel, uint8_t level);  // 0-255
void setSleep(bool sleep);                    // panels off/on (DISPOFF/DISPON)

// Streams a frame strip by strip. Per panel usage (one task per panel is fine):
//   uint16_t* buf = beginStrip(p);  fill kWidth*kStripRows pixels (big-endian RGB565)
//   pushStrip(p, y0);               queues DMA; the next beginStrip() returns the other buffer
// finishFrame(p) waits for all queued strips of that panel.
uint16_t* beginStrip(int panel);
void pushStrip(int panel, int y0);
void finishFrame(int panel);

}  // namespace display
