#!/usr/bin/env bash
set -euo pipefail

APP_NAME="gasmonitor"
INSTALL_DIR="${INSTALL_DIR:-/opt/${APP_NAME}}"
CONFIG_DIR="${CONFIG_DIR:-/var/lib/${APP_NAME}}"
CONFIG_PATH="${CONFIG_PATH:-${CONFIG_DIR}/config.ini}"
SERVICE_NAME="${SERVICE_NAME:-gasmonitor.service}"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"
APP_USER="${APP_USER:-pi}"
APP_GROUP="${APP_GROUP:-${APP_USER}}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
ENABLE_INTERFACES="${ENABLE_INTERFACES:-1}"
BACKUP_ROOT="${BACKUP_ROOT:-/var/backups/${APP_NAME}}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this installer with sudo."
  exit 1
fi

backup_file() {
  local path="$1"
  if [[ -f "${path}" ]]; then
    mkdir -p "${BACKUP_ROOT}/${TIMESTAMP}"
    cp -a "${path}" "${BACKUP_ROOT}/${TIMESTAMP}/$(basename "${path}")"
  fi
}

same_path() {
  local left right
  left="$(cd "$1" 2>/dev/null && pwd -P)" || return 1
  right="$(cd "$2" 2>/dev/null && pwd -P)" || return 1
  [[ "${left}" == "${right}" ]]
}

echo "[1/11] Installing OS packages..."
apt-get update
apt-get install -y \
  git \
  rsync \
  build-essential \
  pkg-config \
  python3 \
  python3-venv \
  python3-pip \
  python3-dev \
  libffi-dev \
  libjpeg-dev \
  libopenjp2-7 \
  zlib1g-dev \
  i2c-tools \
  python3-smbus

echo "[2/11] Enabling I2C and SPI..."
if [[ "${ENABLE_INTERFACES}" == "1" ]] && command -v raspi-config >/dev/null 2>&1; then
  raspi-config nonint do_i2c 0 || true
  raspi-config nonint do_spi 0 || true
fi

echo "[3/11] Granting hardware access groups..."
usermod -aG i2c,spi,video,input "${APP_USER}" || true

echo "[4/11] Stopping existing service if present..."
if systemctl list-unit-files "${SERVICE_NAME}" >/dev/null 2>&1; then
  systemctl stop "${SERVICE_NAME}" || true
fi

echo "[5/11] Backing up existing install and configuration..."
backup_file "${CONFIG_PATH}"
backup_file "${INSTALL_DIR}/config.ini"
backup_file "${SERVICE_PATH}"

echo "[6/11] Copying application into ${INSTALL_DIR}..."
mkdir -p "${INSTALL_DIR}"
if same_path "${SCRIPT_DIR}" "${INSTALL_DIR}"; then
  echo "Source and destination are the same path; skipping file sync."
else
  rsync -a --delete \
    --exclude '.git' \
    --exclude '.venv' \
    --exclude 'config.ini' \
    --exclude 'logs/' \
    --exclude '__pycache__' \
    "${SCRIPT_DIR}/" "${INSTALL_DIR}/"
fi

echo "[7/11] Migrating runtime configuration..."
mkdir -p "${INSTALL_DIR}/logs"
mkdir -p "${CONFIG_DIR}"
if [[ ! -f "${CONFIG_PATH}" ]]; then
  if [[ -f "${INSTALL_DIR}/config.ini" ]]; then
    echo "Migrating legacy ${INSTALL_DIR}/config.ini to ${CONFIG_PATH}"
    install -m 0644 "${INSTALL_DIR}/config.ini" "${CONFIG_PATH}"
  else
    install -m 0644 "${SCRIPT_DIR}/config.ini" "${CONFIG_PATH}"
  fi
elif [[ -f "${INSTALL_DIR}/config.ini" ]]; then
  echo "Runtime config already exists; legacy ${INSTALL_DIR}/config.ini was backed up and left untouched."
fi

echo "[8/11] Creating virtual environment..."
if [[ ! -x "${INSTALL_DIR}/.venv/bin/python" ]]; then
  "${PYTHON_BIN}" -m venv "${INSTALL_DIR}/.venv"
fi

echo "[9/11] Installing Python dependencies..."
"${INSTALL_DIR}/.venv/bin/pip" install --upgrade pip
"${INSTALL_DIR}/.venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

echo "[10/11] Validating and auto-repairing runtime config..."
(cd "${INSTALL_DIR}" && "${INSTALL_DIR}/.venv/bin/python" - <<PY
from config import ConfigManager
ConfigManager("${CONFIG_PATH}")
PY
)

echo "[11/11] Installing and starting systemd service..."
sed \
  -e "s|__APP_USER__|${APP_USER}|g" \
  -e "s|__APP_GROUP__|${APP_GROUP}|g" \
  -e "s|__INSTALL_DIR__|${INSTALL_DIR}|g" \
  -e "s|__CONFIG_PATH__|${CONFIG_PATH}|g" \
  "${INSTALL_DIR}/gasmonitor.service" > "${SERVICE_PATH}"
chown -R "${APP_USER}:${APP_GROUP}" "${INSTALL_DIR}"
chown -R "${APP_USER}:${APP_GROUP}" "${CONFIG_DIR}"
chmod 0644 "${SERVICE_PATH}"
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

echo "Installation complete. Check with: sudo systemctl status ${SERVICE_NAME}"
echo "Runtime config: ${CONFIG_PATH}"
echo "Backups, if any: ${BACKUP_ROOT}/${TIMESTAMP}"
