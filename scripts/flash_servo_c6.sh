#!/usr/bin/env bash
# flash_servo_c6.sh — build + flash + monitor the servo-node firmware on
# ESP32-C6 via USB-Serial-JTAG.
#
# Usage:
#   scripts/flash_servo_c6.sh                  # auto-detect /dev/ttyACM0
#   scripts/flash_servo_c6.sh /dev/ttyACM1     # explicit port
#   scripts/flash_servo_c6.sh /dev/ttyACM0 --clean   # wipe build/ first
#
# Exit codes:
#   0 ok, 1 IDF not found, 2 port not present, 3 build failed,
#   4 flash failed, 5 port busy.

set -euo pipefail

IDF_DIR="${HOME}/esp/esp-idf-v5.4"
PORT="${1:-/dev/ttyACM0}"
CLEAN=""
if [[ "${2:-}" == "--clean" ]]; then CLEAN=1; fi

FW_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/firmware"

echo "[flash] firmware dir : $FW_DIR"
echo "[flash] IDF dir      : $IDF_DIR"
echo "[flash] port         : $PORT"
echo "[flash] clean build  : ${CLEAN:-no}"

if [[ ! -f "$IDF_DIR/export.sh" ]]; then
    echo "[flash] ERROR: ESP-IDF v5.4 not at $IDF_DIR" >&2
    exit 1
fi

if [[ ! -e "$PORT" ]]; then
    echo "[flash] ERROR: port $PORT not present (lsusb / dmesg | tail to check)" >&2
    exit 2
fi

if command -v lsof >/dev/null 2>&1; then
    if lsof "$PORT" >/dev/null 2>&1; then
        echo "[flash] ERROR: $PORT is in use:" >&2
        lsof "$PORT" >&2 || true
        echo "[flash] kill the holder (idf.py monitor, screen, minicom) and retry" >&2
        exit 5
    fi
elif command -v fuser >/dev/null 2>&1; then
    if fuser "$PORT" >/dev/null 2>&1; then
        echo "[flash] ERROR: $PORT is in use:" >&2
        fuser -v "$PORT" >&2 || true
        exit 5
    fi
else
    echo "[flash] WARN: neither lsof nor fuser installed — port-in-use check skipped" >&2
fi

# shellcheck disable=SC1091
source "$IDF_DIR/export.sh"

cd "$FW_DIR"

if [[ -n "$CLEAN" ]]; then
    echo "[flash] wiping build/ managed_components/ dependencies.lock sdkconfig"
    rm -rf build/ managed_components/ dependencies.lock sdkconfig
fi

if [[ ! -f sdkconfig ]] || ! grep -q 'CONFIG_IDF_TARGET="esp32c6"' sdkconfig 2>/dev/null; then
    echo "[flash] setting target esp32c6"
    idf.py set-target esp32c6
fi

echo "[flash] building"
if ! idf.py build; then
    echo "[flash] ERROR: build failed" >&2
    exit 3
fi

echo "[flash] flashing $PORT"
if ! idf.py -p "$PORT" flash; then
    echo "[flash] ERROR: flash failed" >&2
    exit 4
fi

echo "[flash] starting monitor (Ctrl-] to exit)"
exec idf.py -p "$PORT" monitor
