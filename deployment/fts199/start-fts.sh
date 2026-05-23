#!/bin/bash
# Launch FreeTAKServer 1.9.9.6.
#
# 1.9.9.6's MainConfig is environment-variable driven once a YAML config file
# exists at FTS_CONFIG_PATH: every setting falls back to its FTS_* env var, so
# the compose `environment:` block configures the server. The only wrinkles to
# tame at startup are the package's *interactive* first-run config wizard and
# its insistence on a YAML config file:
#
#   1. On a fresh install MainConfig.first_start is True, so FTS.py calls
#      ask_user_for_config(), which blocks on input() reading stdin. In a
#      detached container stdin is EOF and the process dies with
#      "EOF when reading a line" before the CoT listener ever binds. We flip
#      first_start -> False in the installed package so the wizard never runs.
#   2. MainConfig only honours the FTS_* env vars when a YAML config file is
#      present at yaml_path (default /opt/FTSConfig.yaml, override FTS_CONFIG_PATH);
#      otherwise it hard-codes the DB to /opt/FreeTAKServer.db. We pre-seed a
#      minimal config on the persistent volume so the env-driven path is taken
#      and the DB lands on the mounted volume.
#
# The CoT TCP listener is :8087, the REST API :19023. 1.9.x accepts plain
# marker CoT over TCP with no presence handshake / IAM dance and persists
# every accepted event to the SQLite DB (FTS_DB_PATH) when FTS_COT_TO_DB is
# true — exactly the behaviour the both3 "Send to troops" path needs.
set -e

mkdir -p /opt/fts

CONFIG_PATH="${FTS_CONFIG_PATH:-/opt/fts/FTSConfig.yaml}"
export FTS_CONFIG_PATH="$CONFIG_PATH"
DB_PATH="${FTS_DB_PATH:-/opt/fts/FTSDataBase.db}"
export FTS_DB_PATH="$DB_PATH"

# (1) Disable the interactive first-run wizard in the installed package.
MAINCONFIG=/usr/local/lib/python3.8/dist-packages/FreeTAKServer/controllers/configuration/MainConfig.py
if grep -q "first_start = True" "$MAINCONFIG"; then
  sed -i 's/    first_start = True/    first_start = False/' "$MAINCONFIG"
  echo "[start-fts] disabled first-run config wizard (first_start=False)"
fi

# (2) Seed a minimal YAML config so MainConfig takes the env-driven branch and
#     the DB path points at the persistent volume. Real values still come from
#     the FTS_* env vars (compose environment:) at import time.
if [ ! -f "$CONFIG_PATH" ]; then
  cat > "$CONFIG_PATH" <<YAML
System:
  FTS_CONNECTION_MESSAGE: ${FTS_CONNECTION_MESSAGE:-Welcome to rfmesh both3 ops}
  FTS_OPTIMIZE_API: ${FTS_OPTIMIZE_API:-True}
  FTS_MAINLOOP_DELAY: ${FTS_MAINLOOP_DELAY:-100}
  FTS_DATABASE_TYPE: SQLite
Addresses:
  FTS_DP_ADDRESS: ${FTS_DP_ADDRESS:-0.0.0.0}
  FTS_USER_ADDRESS: ${FTS_USER_ADDRESS:-0.0.0.0}
FileSystem:
  FTS_DB_PATH: ${DB_PATH}
  FTS_COT_TO_DB: ${FTS_COT_TO_DB:-True}
  FTS_MAINPATH: /usr/local/lib/python3.8/dist-packages/FreeTAKServer
YAML
  echo "[start-fts] seeded config at $CONFIG_PATH"
fi

echo "[start-fts] FreeTAKServer $(python3 -c 'import FreeTAKServer; print(getattr(FreeTAKServer,"__version__","?"))' 2>/dev/null || echo 1.9.9.6)"
echo "[start-fts] CoT port=${FTS_COT_PORT:-8087} API=${FTS_API_ADDRESS:-0.0.0.0}:${FTS_API_PORT:-19023} DB=${DB_PATH} SaveCoTToDB=${FTS_COT_TO_DB:-True}"

# AutoStart True => bring up the full server (CoT + DataPackage + API), not
# just the REST API. IP/port come from the env-driven MainConfig, but we
# pass the IPs explicitly too so the listeners bind to all interfaces.
exec python3 -m FreeTAKServer.controllers.services.FTS \
  -AutoStart True \
  -CoTIP "${FTS_COT_IP:-0.0.0.0}" \
  -CoTPort "${FTS_COT_PORT:-8087}" \
  -RestAPIIP "${FTS_API_ADDRESS:-0.0.0.0}" \
  -RestAPIPort "${FTS_API_PORT:-19023}"
