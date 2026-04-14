#!/usr/bin/env bash
set -euo pipefail

APP_NAME="gasmonitor"
INSTALL_DIR="${INSTALL_DIR:-/opt/${APP_NAME}}"
SERVICE_NAME="${SERVICE_NAME:-gasmonitor.service}"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"
APP_USER="${APP_USER:-pi}"
APP_GROUP="${APP_GROUP:-${APP_USER}}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
ENABLE_INTERFACES="${ENABLE_INTERFACES:-1}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this installer with sudo."
  exit 1
fi

echo "[1/8] Installing OS packages..."
apt-get update
apt-get install -y \
  python3 \
  python3-venv \
  python3-pip \
  python3-dev \
  libjpeg-dev \
  libopenjp2-7 \
  zlib1g-dev \
  i2c-tools \
  python3-smbus \
  rsync

echo "[2/8] Enabling I2C and SPI..."
if [[ "${ENABLE_INTERFACES}" == "1" ]] && command -v raspi-config >/dev/null 2>&1; then
  raspi-config nonint do_i2c 0 || true
  raspi-config nonint do_spi 0 || true
fi

echo "[3/8] Granting hardware access groups..."
usermod -aG i2c,spi,video,input "${APP_USER}" || true

echo "[4/8] Copying application into ${INSTALL_DIR}..."
mkdir -p "${INSTALL_DIR}"
rsync -a --delete \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude 'config.ini' \
  --exclude 'logs/' \
  --exclude '__pycache__' \
  "${SCRIPT_DIR}/" "${INSTALL_DIR}/"

echo "[5/8] Ensuring config and log directories exist..."
mkdir -p "${INSTALL_DIR}/logs"
if [[ ! -f "${INSTALL_DIR}/config.ini" ]]; then
  install -m 0644 "${SCRIPT_DIR}/config.ini" "${INSTALL_DIR}/config.ini"
fi

echo "[6/8] Creating virtual environment..."
if [[ ! -x "${INSTALL_DIR}/.venv/bin/python" ]]; then
  "${PYTHON_BIN}" -m venv "${INSTALL_DIR}/.venv"
fi

echo "[7/8] Installing Python dependencies..."
"${INSTALL_DIR}/.venv/bin/pip" install --upgrade pip
"${INSTALL_DIR}/.venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

echo "[8/8] Installing and starting systemd service..."
sed \
  -e "s|__APP_USER__|${APP_USER}|g" \
  -e "s|__APP_GROUP__|${APP_GROUP}|g" \
  -e "s|__INSTALL_DIR__|${INSTALL_DIR}|g" \
  "${INSTALL_DIR}/gasmonitor.service" > "${SERVICE_PATH}"
chown -R "${APP_USER}:${APP_GROUP}" "${INSTALL_DIR}"
chmod 0644 "${SERVICE_PATH}"
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

echo "Installation complete. Check with: sudo systemctl status ${SERVICE_NAME}"
