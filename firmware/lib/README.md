# Vendored libraries

- `es8311/` (speaker codec) and `es7210/` (microphone ADC): Espressif's codec drivers
  (Apache-2.0, see the SPDX headers in each file) as adapted to Arduino `Wire` by Waveshare in
  https://github.com/waveshareteam/ESP32-S3-DualEye-Touch-LCD-1.28
  (`example/ESP32-S3-DualEye-LCD-1.28/Arduino-3.2.0/libraries`). Local change: `es8311.h` and `es7210.h`
  include `hal/i2c_types.h` instead of the legacy `driver/i2c.h`.
