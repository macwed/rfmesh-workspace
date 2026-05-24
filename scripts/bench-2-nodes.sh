#!/usr/bin/env bash
# bench-2-nodes.sh — bring up backend + 2 nodes for the laptop MVP demo.
#
# Three terminals worth of commands, scripted. Run from repo root:
#
#   bash scripts/bench-2-nodes.sh backend     # terminal 1
#   bash scripts/bench-2-nodes.sh node 1      # terminal 2
#   bash scripts/bench-2-nodes.sh node 2      # terminal 3
#
# Then open http://127.0.0.1:8000/locate.html to see the fusion result and
# http://127.0.0.1:8000/link.html to see the node list + manual-steer slider.
#
# Pre-flight (one-shot, before bringing nodes up):
#
#   bash scripts/bench-2-nodes.sh preflight
#
# Loud-fails (B3) if RTL-SDR / ESP32 / backend deps not present.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

CONFIG_DIR="configs"
CONFIG_1="$CONFIG_DIR/node-laptop-01.yaml"
CONFIG_2="$CONFIG_DIR/node-laptop-02.yaml"
BACKEND_DIR="deployment/backend"
FRONTEND_DIR="$REPO_ROOT/deployment/frontend"

case "${1:-}" in
  preflight)
    echo "== rtl_test =="
    command -v rtl_test >/dev/null || { echo "rtl_test missing — install rtl-sdr"; exit 1; }
    rtl_test -t 2>&1 | grep -E "Found .* device|Serial number" || true
    echo
    echo "== ESP32 servo ports =="
    ls -l /dev/ttyACM* 2>&1 || { echo "no /dev/ttyACM* — plug in ESP32-C6 servo controllers"; exit 1; }
    echo
    echo "== node YAMLs =="
    [[ -f "$CONFIG_1" ]] || { echo "missing $CONFIG_1"; exit 1; }
    [[ -f "$CONFIG_2" ]] || { echo "missing $CONFIG_2"; exit 1; }
    echo "  $CONFIG_1: ok"
    echo "  $CONFIG_2: ok"
    echo
    echo "== backend venv =="
    [[ -d "$BACKEND_DIR/.venv" ]] || { echo "backend .venv missing — run: cd $BACKEND_DIR && uv sync"; exit 1; }
    echo "  $BACKEND_DIR/.venv: ok"
    echo
    echo "Preflight PASSED. Edit the YAML serials + servo_port if needed, then:"
    echo "  bash scripts/bench-2-nodes.sh backend"
    echo "  bash scripts/bench-2-nodes.sh node 1"
    echo "  bash scripts/bench-2-nodes.sh node 2"
    ;;

  backend)
    cd "$BACKEND_DIR"
    echo "Starting backend at http://127.0.0.1:8000 (auto-fuse enabled, SEED_DEMO=false)"
    echo "  locate.html  — http://127.0.0.1:8000/locate.html"
    echo "  link.html    — http://127.0.0.1:8000/link.html"
    FRONTEND_DIR="$FRONTEND_DIR" \
      COT_ENDPOINT_URL="tcp://127.0.0.1:1" \
      SEED_DEMO=false \
      FUSION_ENABLED=true \
      FUSION_BATCH_WINDOW_MS=500 \
      FUSION_MIN_BEARINGS=2 \
      .venv/bin/python -m uvicorn both3_poc.app:app --host 127.0.0.1 --port 8000
    ;;

  node)
    n="${2:-}"
    case "$n" in
      1) cfg="$CONFIG_1" ;;
      2) cfg="$CONFIG_2" ;;
      *) echo "usage: bench-2-nodes.sh node {1|2}"; exit 1 ;;
    esac
    [[ -f "$cfg" ]] || { echo "missing $cfg — copy from configs/node-pi-01.yaml or run preflight first"; exit 1; }
    echo "Starting node from $cfg"
    exec uv run rfmesh-node --config "$cfg"
    ;;

  *)
    cat <<'USAGE'
bench-2-nodes.sh — laptop MVP demo helper

  preflight      RTL-SDR + ESP32 + YAML + backend-venv sanity checks
  backend        FastAPI backend at :8000 with auto-fuse on /bearings ingest
  node 1         rfmesh-node from configs/node-laptop-01.yaml
  node 2         rfmesh-node from configs/node-laptop-02.yaml
USAGE
    exit 1
    ;;
esac
