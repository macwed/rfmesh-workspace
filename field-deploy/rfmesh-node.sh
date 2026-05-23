#!/usr/bin/env bash
# rfmesh-node.sh -- ExecStart for the rfmesh systemd field-node service.
#
# Runs unattended from boot; stdout/stderr go to the systemd journal:
#   journalctl -u rfmesh-node -f
#
# Config is auto-selected from the Pi's hardware serial (last 8 hex digits):
#   configs/node-<serial>.yaml
# Override by setting CONFIG in /etc/rfmesh-node.env if needed.
#
# Other env vars (via /etc/rfmesh-node.env):
#   SERVO_PORT  /dev/ttyACM*  (default: auto-detect first match)
#
# To install the service: sudo bash field-deploy/install-service.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Derive a stable device ID from the CPU serial (/proc/cpuinfo), falling back
# to the hostname. This is what makes SD-card swaps zero-config: each Pi has
# its own configs/node-<id>.yaml and the right one is picked automatically.
_serial="$(grep -m1 '^Serial' /proc/cpuinfo 2>/dev/null | awk '{print $3}' | sed 's/^0*//')"
PI_ID="${_serial:-$(hostname -s)}"
CONFIG="${CONFIG:-configs/node-${PI_ID}.yaml}"
UV="${UV:-$HOME/.local/bin/uv}"
command -v "$UV" >/dev/null 2>&1 || UV=uv

# ---- pre-flight -------------------------------------------------------
# All failures exit non-zero so systemd sees a clean failure and restarts
# after RestartSec. Each check prints a journal-friendly message.

[[ -f "$CONFIG" ]] \
  || { echo "rfmesh-node: config not found: $CONFIG (PI_ID=$PI_ID) — create configs/node-${PI_ID}.yaml for this Pi"; exit 1; }

if command -v rtl_test >/dev/null 2>&1; then
  rtl_test -t 2>&1 | grep -q "Found .* device" \
    || { echo "rfmesh-node: no RTL-SDR found (plugged in? DVB driver blacklisted?)"; exit 1; }
else
  echo "rfmesh-node: rtl_test not on PATH — run deployment/node/setup-node.sh first"; exit 1
fi

# Pre-flight servo presence check. The YAML's `servo_port:` is the
# authoritative source; we just warn if SERVO_PORT is exported but the
# device is absent, so the operator sees a clear systemd-journal line.
if [[ -n "${SERVO_PORT:-}" && ! -e "$SERVO_PORT" ]]; then
  echo "rfmesh-node: WARNING SERVO_PORT=$SERVO_PORT exported but device absent (YAML servo_port: will be used as-is)"
fi

# Backend reachability: warn only — node remains up and retries while running.
_endpoint="$(grep -E '^[[:space:]]*fusion_endpoint:' "$CONFIG" 2>/dev/null | head -1 \
  | sed -E 's/#.*$//; s/^[^:]*:[[:space:]]*//; s/[[:space:]]*$//; s/^"//; s/"$//')"
case "${_endpoint:-}" in
  http://*|https://*)
    if command -v curl >/dev/null 2>&1; then
      curl -fsS --max-time 2 "${_endpoint%/}/health" >/dev/null 2>&1 \
        || echo "rfmesh-node: WARNING backend not reachable at ${_endpoint} (bearings queued until it is up)"
    fi
    ;;
esac

# ADR-022: CLI shrinks to --config + --log-level. servo_port, sweep/*,
# rendezvous/*, command_endpoint live in the YAML. Operators that need
# to override servo_port at deploy time edit the YAML, not the CLI.
echo "rfmesh-node: starting — config=$CONFIG"
exec "$UV" run rfmesh-node --config "$CONFIG"
