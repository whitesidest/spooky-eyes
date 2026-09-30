#!/usr/bin/env bash
# Builds libspookysim.so: the firmware renderer + behaviour engine as a desktop shared library.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
src="$here/../../firmware/src/render"
mkdir -p "$here/build"
g++ -std=c++20 -O2 -fPIC -shared -Wall -Wextra -Wno-missing-field-initializers \
  -I"$src" "$here/sim_api.cpp" "$src"/*.cpp -o "$here/build/libspookysim.so"
