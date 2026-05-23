#!/usr/bin/env bash
# setup-node.sh -- one-time field-node provisioning for a Raspberry Pi (Debian/
# Ubuntu). Installs the RTL-SDR userland, frees the dongle from the kernel DVB
# driver, adds udev rules for non-root access, and syncs the Python workspace.
#
# Idempotent: safe to re-run. Run once per Pi:
#   curl/clone the repo, then:  sudo bash deployment/node/setup-node.sh
#
# After this, plug in the RTL-SDR + ESP32 and run deployment/node/node-up.sh.
set -euo pipefail

ENABLE_AUTOSTART=0
[[ "${1:-}" == "--enable-autostart" ]] && ENABLE_AUTOSTART=1

if [[ $EUID -ne 0 ]]; then
  echo "setup-node.sh: re-run with sudo (it installs packages + udev rules)." >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REAL_USER="${SUDO_USER:-$(id -un)}"

echo "==> [1/5] apt: rtl-sdr userland"
apt-get update -qq
apt-get install -y rtl-sdr curl

echo "==> [2/5] blacklist kernel DVB driver (so rtl_sdr can claim the dongle)"
cat > /etc/modprobe.d/blacklist-rtlsdr.conf <<'EOF'
# rfmesh: the DVB-T kernel driver grabs the RTL2832U; blacklist it so the
# rtl_sdr userland can open the device.
blacklist dvb_usb_rtl28xxu
blacklist rtl2832
blacklist rtl2830
EOF
modprobe -r dvb_usb_rtl28xxu 2>/dev/null || true

echo "==> [3/5] udev: non-root access for RTL-SDR + ESP32 CDC"
cat > /etc/udev/rules.d/99-rfmesh.rules <<'EOF'
# RTL-SDR (Realtek RTL2832U)
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", MODE="0666", GROUP="plugdev"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2832", MODE="0666", GROUP="plugdev"
# ESP32-S2 USB-CDC (Espressif) -- servo controller. dialout group owns ttyACM*.
SUBSYSTEM=="tty", ATTRS{idVendor}=="303a", MODE="0666", GROUP="dialout"
EOF
udevadm control --reload-rules && udevadm trigger
usermod -aG plugdev,dialout "$REAL_USER" || true

echo "==> [4/5] uv (Python toolchain) for $REAL_USER"
if ! sudo -u "$REAL_USER" bash -lc 'command -v uv >/dev/null'; then
  sudo -u "$REAL_USER" bash -lc 'curl -LsSf https://astral.sh/uv/install.sh | sh'
fi

echo "==> [5/6] uv sync (fetch Python 3.12 + workspace deps)"
sudo -u "$REAL_USER" bash -lc "cd '$REPO_ROOT' && \$HOME/.local/bin/uv sync"

if [[ $ENABLE_AUTOSTART -eq 1 ]]; then
  echo "==> [6/6] install + enable boot auto-start (systemd)"
  sed -e "s#@USER@#${REAL_USER}#g" -e "s#@REPO_ROOT@#${REPO_ROOT}#g" \
    "$REPO_ROOT/deployment/node/rfmesh-node.service" \
    > /etc/systemd/system/rfmesh-node.service
  systemctl daemon-reload
  systemctl enable rfmesh-node.service
  echo "    enabled: node will start on boot. 'systemctl start rfmesh-node' to start now."
  echo "    WARNING: only useful once configs/node-pi-01.yaml has real values."
else
  echo "==> [6/6] boot auto-start: skipped (pass --enable-autostart to install it)"
fi

cat <<EOF

OK setup complete.
  * Log out/in (or reboot) so group membership (plugdev/dialout) takes effect.
  * Verify the SDR:    rtl_test -t
  * Bring up the servo: uv run python scripts/servo_bringup.py --port /dev/ttyACM0 --set-default-cal
  * Start the node:     deployment/node/node-up.sh
  * Auto-start at boot: sudo bash deployment/node/setup-node.sh --enable-autostart
                        (do this only after the config has real values)
EOF
