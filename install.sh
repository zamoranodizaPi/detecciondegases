#!/usr/bin/env bash
set -euo pipefail

APP_NAME="oxygen-monitor"
INSTALL_DIR="${INSTALL_DIR:-/opt/${APP_NAME}}"
SERVICE_NAME="${SERVICE_NAME:-oxygen-monitor.service}"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"
APP_USER="${APP_USER:-pi}"
APP_GROUP="${APP_GROUP:-${APP_USER}}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
FRAMEBUFFER="${FRAMEBUFFER:-/dev/fb1}"
WIDTH="${WIDTH:-480}"
HEIGHT="${HEIGHT:-320}"
ROTATE="${ROTATE:-0}"
I2C_BUS="${I2C_BUS:-1}"
I2C_ADDRESS="${I2C_ADDRESS:-0x73}"
MODBUS_PORT="${MODBUS_PORT:-5020}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this installer with sudo."
  exit 1
fi

if ! id "${APP_USER}" >/dev/null 2>&1; then
  echo "User '${APP_USER}' does not exist."
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
  zlib1g-dev \
  i2c-tools \
  python3-smbus

echo "[2/8] Creating installation directory at ${INSTALL_DIR}..."
mkdir -p "${INSTALL_DIR}"

echo "[3/8] Copying application files..."
install -m 0644 "${SCRIPT_DIR}/oxygen_monitor.py" "${INSTALL_DIR}/oxygen_monitor.py"
install -m 0644 "${SCRIPT_DIR}/requirements.txt" "${INSTALL_DIR}/requirements.txt"

echo "[4/8] Creating Python virtual environment..."
"${PYTHON_BIN}" -m venv "${INSTALL_DIR}/.venv"

echo "[5/8] Installing Python dependencies..."
"${INSTALL_DIR}/.venv/bin/pip" install --upgrade pip
"${INSTALL_DIR}/.venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

echo "[6/8] Installing systemd service..."
sed \
  -e "s|__APP_USER__|${APP_USER}|g" \
  -e "s|__APP_GROUP__|${APP_GROUP}|g" \
  -e "s|__INSTALL_DIR__|${INSTALL_DIR}|g" \
  -e "s|__FRAMEBUFFER__|${FRAMEBUFFER}|g" \
  -e "s|__WIDTH__|${WIDTH}|g" \
  -e "s|__HEIGHT__|${HEIGHT}|g" \
  -e "s|__ROTATE__|${ROTATE}|g" \
  -e "s|__I2C_BUS__|${I2C_BUS}|g" \
  -e "s|__I2C_ADDRESS__|${I2C_ADDRESS}|g" \
  -e "s|__MODBUS_PORT__|${MODBUS_PORT}|g" \
  "${SCRIPT_DIR}/oxygen-monitor.service" > "${SERVICE_PATH}"

echo "[7/8] Setting ownership and permissions..."
chown -R "${APP_USER}:${APP_GROUP}" "${INSTALL_DIR}"
chmod 0644 "${SERVICE_PATH}"

echo "[8/8] Enabling and starting service..."
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

echo
echo "Installation completed."
echo "Service status:"
systemctl --no-pager --full status "${SERVICE_NAME}" || true
echo
echo "Live logs:"
echo "  sudo journalctl -u ${SERVICE_NAME} -f"
