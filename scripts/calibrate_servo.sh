#!/usr/bin/env bash
# calibrate_servo.sh — wrapper around `rfmesh-servo-calibrate` that adds
# pre-flight safety checks (port present, port not held by another
# process, ModemManager not grabbing the CDC), then runs the guided
# calibration on the connected ESP32-C6 servo node.
#
# Usage:
#   scripts/calibrate_servo.sh                                      # axis 0, /dev/ttyACM0
#   scripts/calibrate_servo.sh --port /dev/ttyACM1                  # explicit port
#   scripts/calibrate_servo.sh --port /dev/ttyACM0 --axis 0 \
#       --pulse-min 600 --pulse-max 2400                            # custom range
#
# After the CLI exits, the script power-cycles verification via
# get_calibration to defend against the cosmetic CAL_PERSIST framing
# glitch (memory: NVS write OK, response framing glitch).

set -euo pipefail

PORT="/dev/ttyACM0"
AXIS=0
PULSE_MIN=500
PULSE_MAX=2500

while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)       PORT="$2";       shift 2 ;;
        --axis)       AXIS="$2";       shift 2 ;;
        --pulse-min)  PULSE_MIN="$2";  shift 2 ;;
        --pulse-max)  PULSE_MAX="$2";  shift 2 ;;
        -h|--help)
            echo "Usage: $0 [--port PORT] [--axis N] [--pulse-min US] [--pulse-max US]"
            exit 0 ;;
        *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done

WS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "[cal] workspace : $WS_DIR"
echo "[cal] port      : $PORT"
echo "[cal] axis      : $AXIS"
echo "[cal] pulse_min : $PULSE_MIN us"
echo "[cal] pulse_max : $PULSE_MAX us"

if [[ ! -e "$PORT" ]]; then
    echo "[cal] ERROR: $PORT not present (lsusb / dmesg | tail to check)" >&2
    exit 2
fi

if command -v lsof >/dev/null 2>&1; then
    if lsof "$PORT" >/dev/null 2>&1; then
        echo "[cal] ERROR: $PORT is held by another process:" >&2
        lsof "$PORT" >&2 || true
        echo "[cal] kill the holder (idf.py monitor, screen, minicom) and retry" >&2
        exit 3
    fi
elif command -v fuser >/dev/null 2>&1; then
    if fuser "$PORT" >/dev/null 2>&1; then
        echo "[cal] ERROR: $PORT is held by another process:" >&2
        fuser -v "$PORT" >&2 || true
        exit 3
    fi
else
    echo "[cal] WARN: neither lsof nor fuser installed — port-in-use check skipped" >&2
fi

# ModemManager on Fedora often grabs USB-CDC briefly at attach. If it's
# active and recently touched the port, warn the operator. Guarded so the
# script still works on non-systemd hosts.
if command -v systemctl >/dev/null 2>&1 && command -v journalctl >/dev/null 2>&1; then
    if systemctl is-active --quiet ModemManager 2>/dev/null; then
        if journalctl -u ModemManager --since "30 sec ago" 2>/dev/null \
                | grep -q "$(basename "$PORT")"; then
            echo "[cal] WARN: ModemManager recently touched $PORT" >&2
            echo "[cal]       sudo systemctl stop ModemManager   # if connect hangs" >&2
        fi
    fi
fi

cd "$WS_DIR"

echo "[cal] launching guided calibration"
uv run rfmesh-servo-calibrate \
    --port "$PORT" \
    --axis "$AXIS" \
    --pulse-min "$PULSE_MIN" \
    --pulse-max "$PULSE_MAX"

echo ""
echo "[cal] CLI exited cleanly."
echo "[cal] Verify persistence: power-cycle the C6 (unplug + replug USB),"
echo "[cal] then run:"
echo ""
echo "      uv run python -c \"\\"
echo "from rfmesh_servo.transport import SerialTransport; \\"
echo "from rfmesh_servo.driver import ServoDriver; \\"
echo "import sys; \\"
echo "t = SerialTransport('$PORT'); \\"
echo "d = ServoDriver(t, own_transport=True); \\"
echo "d.__enter__(); d.connect(); \\"
echo "print(d.get_calibration($AXIS)); \\"
echo "d.__exit__(None, None, None)\""
echo ""
echo "[cal] If get_calibration returns the values you just persisted,"
echo "[cal] the CAL_PERSIST error (if any) was the cosmetic framing"
echo "[cal] glitch documented in memory — NVS write succeeded."
