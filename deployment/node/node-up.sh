#!/usr/bin/env bash
# node-up.sh -- the one command that brings a field L1 node online.
#
# Checks the RTL-SDR is present, auto-detects the ESP32 servo serial port,
# and launches rfmesh-node with the L1 sweep. Plug in cables, run this.
#
#   deployment/node/node-up.sh                         # uses configs/node-pi-01.yaml
#   CONFIG=configs/node-west.yaml deployment/node/node-up.sh
#   SERVO_PORT=/dev/ttyACM1 deployment/node/node-up.sh  # override autodetect
#
# Everything downstream (storing the bearing, drawing the line-of-bearing on
# the map) is handled by the backend named in the config's fusion_endpoint.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

CONFIG="${CONFIG:-configs/node-pi-01.yaml}"
UV="${UV:-$HOME/.local/bin/uv}"
command -v "$UV" >/dev/null 2>&1 || UV=uv

ok()   { printf '  [ OK ] %s\n' "$1"; }
warn() { printf '  [WARN] %s\n' "$1" >&2; }
fail() { printf '  [FAIL] %s\n' "$1" >&2; exit 1; }

echo "==> node-up: pre-flight"

[[ -f "$CONFIG" ]] || fail "config not found: $CONFIG"
ok "config: $CONFIG"

# 1. RTL-SDR present? rtl_test -t returns after enumerating; grep for a device.
if command -v rtl_test >/dev/null 2>&1; then
  if rtl_test -t 2>&1 | grep -q "Found .* device"; then
    ok "RTL-SDR detected"
  else
    fail "no RTL-SDR found (rtl_test saw no device). Plugged in? DVB driver blacklisted? Re-run setup-node.sh"
  fi
else
  fail "rtl_test not on PATH -- run deployment/node/setup-node.sh first"
fi

# 2. Servo serial port: explicit override, else first /dev/ttyACM*.
SERVO_PORT="${SERVO_PORT:-}"
if [[ -z "$SERVO_PORT" ]]; then
  for p in /dev/ttyACM0 /dev/ttyACM1 /dev/ttyACM2; do
    [[ -e "$p" ]] && { SERVO_PORT="$p"; break; }
  done
fi
[[ -n "$SERVO_PORT" && -e "$SERVO_PORT" ]] || fail "no ESP32 servo port found (looked for /dev/ttyACM*). Set SERVO_PORT=..."
ok "servo port: $SERVO_PORT"

# 3. Backend reachable? Warn only -- HttpBearer is best-effort and the
# operator may bring the backend up after the node. A WARN here is the
# difference between "node looks up but delivers nothing" and knowing why.
# Strip trailing "# comment", drop the "key:" prefix (first colon only -- the
# URL's own colons survive), trim space, then strip surrounding quotes.
ENDPOINT="$(grep -E '^[[:space:]]*fusion_endpoint:' "$CONFIG" | head -1 \
  | sed -E 's/#.*$//; s/^[^:]*:[[:space:]]*//; s/[[:space:]]*$//; s/^"//; s/"$//')"
case "$ENDPOINT" in
  http://*|https://*)
    HEALTH="${ENDPOINT%/}/health"
    if command -v curl >/dev/null 2>&1 && curl -fsS --max-time 2 "$HEALTH" >/dev/null 2>&1; then
      ok "backend reachable: $ENDPOINT"
    else
      warn "backend NOT reachable at $ENDPOINT (bearings will be dropped until it is up)"
    fi
    ;;
  *)
    ok "fusion endpoint: $ENDPOINT (non-http; skipping reachability probe)"
    ;;
esac

echo "==> node-up: starting rfmesh-node (Ctrl-C to stop)"
exec "$UV" run rfmesh-node --config "$CONFIG" --servo-port "$SERVO_PORT" "$@"
