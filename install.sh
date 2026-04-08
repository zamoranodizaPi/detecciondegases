#!/usr/bin/env bash
set -euo pipefail

APP_NAME="oxygen-monitor"
INSTALL_DIR="${INSTALL_DIR:-/opt/${APP_NAME}}"
SERVICE_NAME="${SERVICE_NAME:-oxygen-monitor.service}"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"
CONFIG_PATH="${INSTALL_DIR}/oxygen-monitor.conf"
CONFIG_EXAMPLE_PATH="${INSTALL_DIR}/oxygen-monitor.conf.example"
APP_USER="${APP_USER:-pi}"
APP_GROUP="${APP_GROUP:-${APP_USER}}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
FRAMEBUFFER="${FRAMEBUFFER:-/dev/fb1}"
WIDTH="${WIDTH:-480}"
HEIGHT="${HEIGHT:-320}"
ROTATE="${ROTATE:-0}"
I2C_BUS="${I2C_BUS:-1}"
I2C_ADDRESS="${I2C_ADDRESS:-0x73}"
MODBUS_HOST="${MODBUS_HOST:-0.0.0.0}"
MODBUS_PORT="${MODBUS_PORT:-5020}"
MODBUS_REGISTER_ADDRESS="${MODBUS_REGISTER_ADDRESS:-0}"
SAMPLES="${SAMPLES:-10}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"
MEASUREMENT_CALIBRATION_FACTOR="${MEASUREMENT_CALIBRATION_FACTOR:-0.774}"
MAX_VALID_OXYGEN_PERCENT="${MAX_VALID_OXYGEN_PERCENT:-25.0}"
ENABLE_INTERFACES="${ENABLE_INTERFACES:-1}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_IS_GIT_REPO=0
TARGET_IS_GIT_REPO=0

if git -C "${SCRIPT_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  SOURCE_IS_GIT_REPO=1
fi

if [[ -d "${INSTALL_DIR}" ]] && git -C "${INSTALL_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  TARGET_IS_GIT_REPO=1
fi

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this installer with sudo."
  exit 1
fi

if ! id "${APP_USER}" >/dev/null 2>&1; then
  echo "User '${APP_USER}' does not exist."
  exit 1
fi

require_file() {
  local file_path="$1"
  if [[ ! -f "${file_path}" ]]; then
    echo "Required file not found: ${file_path}"
    exit 1
  fi
}

require_file "${SCRIPT_DIR}/oxygen_monitor.py"
require_file "${SCRIPT_DIR}/requirements.txt"
require_file "${SCRIPT_DIR}/oxygen-monitor.service"
require_file "${SCRIPT_DIR}/oxygen-monitor.conf"

install_if_needed() {
  local mode="$1"
  local source_path="$2"
  local destination_path="$3"
  if [[ "${source_path}" == "${destination_path}" ]]; then
    return
  fi
  install -m "${mode}" "${source_path}" "${destination_path}"
}

if [[ "${SOURCE_IS_GIT_REPO}" -eq 0 ]]; then
  echo "Warning: ${SCRIPT_DIR} is not a Git clone."
  echo "Future 'git pull' updates will not work unless you install from a cloned repository."
fi

if [[ "${SOURCE_IS_GIT_REPO}" -eq 1 && "${TARGET_IS_GIT_REPO}" -eq 0 && "${SCRIPT_DIR}" != "${INSTALL_DIR}" ]]; then
  echo "Warning: files will be copied into ${INSTALL_DIR}, but that directory is not a Git clone."
  echo "Recommended deployment:"
  echo "  sudo git clone https://github.com/zamoranodizaPi/detecciondegases.git ${INSTALL_DIR}"
  echo "  cd ${INSTALL_DIR}"
  echo "  sudo ./install.sh"
  echo
fi

echo "[1/10] Installing OS packages..."
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
  python3-smbus

echo "[2/10] Enabling I2C and SPI..."
if [[ "${ENABLE_INTERFACES}" == "1" ]] && command -v raspi-config >/dev/null 2>&1; then
  raspi-config nonint do_i2c 0 || true
  raspi-config nonint do_spi 0 || true
else
  echo "Skipping automatic interface enable. Set ENABLE_INTERFACES=1 and ensure raspi-config exists to enable it automatically."
fi

echo "[3/10] Adding ${APP_USER} to hardware access groups..."
usermod -aG i2c,spi,video "${APP_USER}" || true

echo "[4/10] Creating installation directory at ${INSTALL_DIR}..."
mkdir -p "${INSTALL_DIR}"

echo "[5/10] Copying application files..."
install_if_needed 0644 "${SCRIPT_DIR}/oxygen_monitor.py" "${INSTALL_DIR}/oxygen_monitor.py"
install_if_needed 0644 "${SCRIPT_DIR}/requirements.txt" "${INSTALL_DIR}/requirements.txt"
install_if_needed 0755 "${SCRIPT_DIR}/update.sh" "${INSTALL_DIR}/update.sh"
install_if_needed 0644 "${SCRIPT_DIR}/oxygen-monitor.service" "${INSTALL_DIR}/oxygen-monitor.service.template"
install_if_needed 0644 "${SCRIPT_DIR}/oxygen-monitor.conf" "${CONFIG_EXAMPLE_PATH}"

config_created=0
if [[ ! -f "${CONFIG_PATH}" ]]; then
  install -m 0644 "${SCRIPT_DIR}/oxygen-monitor.conf" "${CONFIG_PATH}"
  config_created=1
fi

if [[ "${config_created}" -eq 1 ]]; then
  sed -i.bak \
    -e "s|^I2C_BUS=.*|I2C_BUS=${I2C_BUS}|" \
    -e "s|^I2C_ADDRESS=.*|I2C_ADDRESS=${I2C_ADDRESS}|" \
    -e "s|^MODBUS_HOST=.*|MODBUS_HOST=${MODBUS_HOST}|" \
    -e "s|^MODBUS_PORT=.*|MODBUS_PORT=${MODBUS_PORT}|" \
    -e "s|^MODBUS_REGISTER_ADDRESS=.*|MODBUS_REGISTER_ADDRESS=${MODBUS_REGISTER_ADDRESS}|" \
    -e "s|^FRAMEBUFFER=.*|FRAMEBUFFER=${FRAMEBUFFER}|" \
    -e "s|^WIDTH=.*|WIDTH=${WIDTH}|" \
    -e "s|^HEIGHT=.*|HEIGHT=${HEIGHT}|" \
    -e "s|^ROTATE=.*|ROTATE=${ROTATE}|" \
    -e "s|^SAMPLES=.*|SAMPLES=${SAMPLES}|" \
    -e "s|^LOG_LEVEL=.*|LOG_LEVEL=${LOG_LEVEL}|" \
    -e "s|^MEASUREMENT_CALIBRATION_FACTOR=.*|MEASUREMENT_CALIBRATION_FACTOR=${MEASUREMENT_CALIBRATION_FACTOR}|" \
    -e "s|^MAX_VALID_OXYGEN_PERCENT=.*|MAX_VALID_OXYGEN_PERCENT=${MAX_VALID_OXYGEN_PERCENT}|" \
    "${CONFIG_PATH}"
  rm -f "${CONFIG_PATH}.bak"
fi

echo "[6/10] Creating Python virtual environment..."
if [[ ! -x "${INSTALL_DIR}/.venv/bin/python" ]]; then
  "${PYTHON_BIN}" -m venv "${INSTALL_DIR}/.venv"
fi

echo "[7/10] Installing Python dependencies..."
"${INSTALL_DIR}/.venv/bin/pip" install --upgrade pip
"${INSTALL_DIR}/.venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

echo "[8/10] Installing systemd service..."
sed \
  -e "s|__APP_USER__|${APP_USER}|g" \
  -e "s|__APP_GROUP__|${APP_GROUP}|g" \
  -e "s|__INSTALL_DIR__|${INSTALL_DIR}|g" \
  "${SCRIPT_DIR}/oxygen-monitor.service" > "${SERVICE_PATH}"

echo "[9/10] Setting ownership and permissions..."
chown -R "${APP_USER}:${APP_GROUP}" "${INSTALL_DIR}"
chmod 0644 "${SERVICE_PATH}"

echo "[10/10] Enabling and starting service..."
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

echo
echo "Installation completed."
if [[ "${ENABLE_INTERFACES}" == "1" ]]; then
  echo "If this is the first install and I2C/SPI were just enabled, a reboot is recommended:"
  echo "  sudo reboot"
  echo
fi
echo "Service status:"
systemctl --no-pager --full status "${SERVICE_NAME}" || true
echo
echo "Live logs:"
echo "  sudo journalctl -u ${SERVICE_NAME} -f"
