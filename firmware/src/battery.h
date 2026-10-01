// Li-ion battery monitor (voltage via the on-board divider -> approximate state of charge).
#pragma once

namespace battery {
void begin();
void update();  // call regularly; samples every few seconds
float volts();    // 0 if not measured yet
int percent();    // 0-100, -1 if unknown
bool present();   // false when no cell is connected (voltage reads near 0 or USB-only)
}  // namespace battery
