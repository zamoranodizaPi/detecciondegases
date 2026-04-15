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
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this updater with sudo."
  exit 1
fi

same_path() {
  local left right
  left="$(cd "$1" 2>/dev/null && pwd -P)" || return 1
  right="$(cd "$2" 2>/dev/null && pwd -P)" || return 1
  [[ "${left}" == "${right}" ]]
}

echo "[1/7] Syncing files..."
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

echo "[2/7] Ensuring runtime directories exist..."
mkdir -p "${INSTALL_DIR}/logs"
mkdir -p "${CONFIG_DIR}"
if [[ ! -f "${CONFIG_PATH}" ]]; then
  install -m 0644 "${SCRIPT_DIR}/config.ini" "${CONFIG_PATH}"
fi

echo "[3/7] Ensuring virtual environment exists..."
if [[ ! -x "${INSTALL_DIR}/.venv/bin/python" ]]; then
  "${PYTHON_BIN}" -m venv "${INSTALL_DIR}/.venv"
fi

echo "[4/7] Updating Python packages..."
"${INSTALL_DIR}/.venv/bin/pip" install --upgrade pip
"${INSTALL_DIR}/.venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

echo "[5/7] Validating and auto-repairing runtime config..."
(cd "${INSTALL_DIR}" && "${INSTALL_DIR}/.venv/bin/python" - <<PY
from config import ConfigManager
ConfigManager("${CONFIG_PATH}")
PY
)

echo "[6/7] Refreshing systemd unit..."
sed \
  -e "s|__APP_USER__|${APP_USER}|g" \
  -e "s|__APP_GROUP__|${APP_GROUP}|g" \
  -e "s|__INSTALL_DIR__|${INSTALL_DIR}|g" \
  -e "s|__CONFIG_PATH__|${CONFIG_PATH}|g" \
  "${INSTALL_DIR}/gasmonitor.service" > "${SERVICE_PATH}"
chown -R "${APP_USER}:${APP_GROUP}" "${INSTALL_DIR}"
chown -R "${APP_USER}:${APP_GROUP}" "${CONFIG_DIR}"
chmod 0644 "${SERVICE_PATH}"

echo "[7/7] Restarting service..."
systemctl daemon-reload
systemctl restart "${SERVICE_NAME}"
systemctl --no-pager --full status "${SERVICE_NAME}" || true
