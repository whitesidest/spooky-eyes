#!/usr/bin/env bash
# Builds the desktop preview of the firmware renderer.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
src="$here/../../firmware/src/render"
mkdir -p "$here/build"
g++ -std=c++20 -O2 -Wall -Wextra -I"$src" "$here/preview.cpp" "$src"/*.cpp -o "$here/build/preview"
