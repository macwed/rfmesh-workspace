#!/usr/bin/env bash
# install-service.sh -- register rfmesh-node.service with systemd.
#
# Run once per Orange Pi (must be root):
#   sudo bash field-deploy/install-service.sh
#
# After this the node starts automatically on every boot.  To control it:
#   systemctl status  rfmesh-node
#   systemctl start   rfmesh-node
#   systemctl stop    rfmesh-node
#   journalctl -u     rfmesh-node -f
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "install-service.sh: re-run with sudo." >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REAL_USER="${SUDO_USER:-$(id -un)}"
SERVICE_SRC="$REPO_ROOT/field-deploy/rfmesh-node.service"
SERVICE_DST="/etc/systemd/system/rfmesh-node.service"
ENV_EXAMPLE="$REPO_ROOT/field-deploy/rfmesh-node.env.example"
ENV_DST="/etc/rfmesh-node.env"

echo "==> Installing rfmesh-node.service for user=$REAL_USER repo=$REPO_ROOT"

# Substitute placeholders and install the unit.
sed -e "s#@USER@#${REAL_USER}#g" -e "s#@REPO_ROOT@#${REPO_ROOT}#g" \
  "$SERVICE_SRC" > "$SERVICE_DST"
echo "    wrote $SERVICE_DST"

# Install env file only if it does not already exist (preserve operator edits).
if [[ ! -f "$ENV_DST" ]]; then
  cp "$ENV_EXAMPLE" "$ENV_DST"
  echo "    wrote $ENV_DST  <-- edit CONFIG= for this Pi"
else
  echo "    $ENV_DST already exists, not overwritten"
fi

chmod +x "$REPO_ROOT/field-deploy/rfmesh-node.sh"

systemctl daemon-reload
systemctl enable rfmesh-node.service
echo "==> Done. Node will start on next boot."
echo "    Edit /etc/rfmesh-node.env, then: systemctl start rfmesh-node"
