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
INSTALL_LCD_DRIVER="${INSTALL_LCD_DRIVER:-auto}"
LCD_SHOW_REPO="${LCD_SHOW_REPO:-https://github.com/goodtft/LCD-show.git}"
LCD_SHOW_DIR="${LCD_SHOW_DIR:-/opt/LCD-show}"
LCD_DRIVER_SCRIPT="${LCD_DRIVER_SCRIPT:-LCD35-show}"
LCD_ROTATION="${LCD_ROTATION:-0}"
APPLY_TOUCH_DEFAULTS="${APPLY_TOUCH_DEFAULTS:-1}"
DISPLAY_FRAMEBUFFER="${DISPLAY_FRAMEBUFFER:-/dev/fb1}"
DISPLAY_WIDTH="${DISPLAY_WIDTH:-320}"
DISPLAY_HEIGHT="${DISPLAY_HEIGHT:-480}"
DISPLAY_ROTATE="${DISPLAY_ROTATE:-0}"
TOUCH_ROTATION="${TOUCH_ROTATION:-90}"
TOUCH_SWAP_XY="${TOUCH_SWAP_XY:-true}"
TOUCH_INVERT_X="${TOUCH_INVERT_X:-false}"
TOUCH_INVERT_Y="${TOUCH_INVERT_Y:-true}"
TOUCH_DEBUG="${TOUCH_DEBUG:-false}"
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

should_install_lcd_driver() {
  case "${INSTALL_LCD_DRIVER}" in
    1|true|TRUE|yes|YES) return 0 ;;
    0|false|FALSE|no|NO) return 1 ;;
    auto|AUTO)
      [[ ! -e "${DISPLAY_FRAMEBUFFER}" ]]
      return
      ;;
    *)
      echo "Invalid INSTALL_LCD_DRIVER=${INSTALL_LCD_DRIVER}; use auto, 1, or 0."
      exit 1
      ;;
  esac
}

install_lcd_driver() {
  if ! should_install_lcd_driver; then
    echo "LCD driver install skipped. Set INSTALL_LCD_DRIVER=1 to force it."
    return
  fi

  echo "Installing 3.5 inch LCD driver with ${LCD_DRIVER_SCRIPT} rotation=${LCD_ROTATION}..."
  if [[ -d "${LCD_SHOW_DIR}/.git" ]]; then
    git -C "${LCD_SHOW_DIR}" pull --ff-only || true
  else
    mkdir -p "$(dirname "${LCD_SHOW_DIR}")"
    git clone "${LCD_SHOW_REPO}" "${LCD_SHOW_DIR}"
  fi
  chmod -R 755 "${LCD_SHOW_DIR}" || true
  if [[ ! -x "${LCD_SHOW_DIR}/${LCD_DRIVER_SCRIPT}" ]]; then
    echo "LCD driver script not found: ${LCD_SHOW_DIR}/${LCD_DRIVER_SCRIPT}"
    echo "Available scripts:"
    find "${LCD_SHOW_DIR}" -maxdepth 1 -type f -name '*show' -printf '%f\n' | sort || true
    exit 1
  fi
  (
    cd "${LCD_SHOW_DIR}"
    "./${LCD_DRIVER_SCRIPT}" "${LCD_ROTATION}"
  )
}

echo "[1/12] Installing OS packages..."
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

echo "[2/12] Enabling I2C and SPI..."
if [[ "${ENABLE_INTERFACES}" == "1" ]] && command -v raspi-config >/dev/null 2>&1; then
  raspi-config nonint do_i2c 0 || true
  raspi-config nonint do_spi 0 || true
fi

echo "[3/12] Granting hardware access groups..."
usermod -aG i2c,spi,video,input "${APP_USER}" || true

echo "[4/12] Stopping existing service if present..."
if systemctl list-unit-files "${SERVICE_NAME}" >/dev/null 2>&1; then
  systemctl stop "${SERVICE_NAME}" || true
fi

echo "[5/12] Backing up existing install and configuration..."
backup_file "${CONFIG_PATH}"
backup_file "${INSTALL_DIR}/config.ini"
backup_file "${SERVICE_PATH}"

echo "[6/12] Copying application into ${INSTALL_DIR}..."
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

echo "[7/12] Migrating runtime configuration..."
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

echo "[8/12] Creating virtual environment..."
if [[ ! -x "${INSTALL_DIR}/.venv/bin/python" ]]; then
  "${PYTHON_BIN}" -m venv "${INSTALL_DIR}/.venv"
fi

echo "[9/12] Installing Python dependencies..."
"${INSTALL_DIR}/.venv/bin/pip" install --upgrade pip
"${INSTALL_DIR}/.venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

echo "[10/12] Validating display/touch config and auto-repairing runtime config..."
(cd "${INSTALL_DIR}" && "${INSTALL_DIR}/.venv/bin/python" - <<PY
from config import ConfigManager
manager = ConfigManager("${CONFIG_PATH}")
if "${APPLY_TOUCH_DEFAULTS}" in ("1", "true", "TRUE", "yes", "YES"):
    manager.update({
        "hardware": {
            "framebuffer": "${DISPLAY_FRAMEBUFFER}",
            "display_width": "${DISPLAY_WIDTH}",
            "display_height": "${DISPLAY_HEIGHT}",
            "display_rotate": "${DISPLAY_ROTATE}",
            "touch_rotation": "${TOUCH_ROTATION}",
            "touch_swap_xy": "${TOUCH_SWAP_XY}",
            "touch_invert_x": "${TOUCH_INVERT_X}",
            "touch_invert_y": "${TOUCH_INVERT_Y}",
            "touch_debug": "${TOUCH_DEBUG}",
        }
    })
PY
)

echo "[11/12] Installing systemd service..."
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

echo "[12/12] Installing LCD driver and starting service..."
install_lcd_driver
systemctl restart "${SERVICE_NAME}"

echo "Installation complete. Check with: sudo systemctl status ${SERVICE_NAME}"
echo "Runtime config: ${CONFIG_PATH}"
echo "Backups, if any: ${BACKUP_ROOT}/${TIMESTAMP}"
