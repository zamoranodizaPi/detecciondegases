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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

install_if_needed() {
  local mode="$1"
  local source_path="$2"
  local destination_path="$3"
  if [[ "${source_path}" == "${destination_path}" ]]; then
    return
  fi
  install -m "${mode}" "${source_path}" "${destination_path}"
}

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this updater with sudo."
  exit 1
fi

if [[ ! -d "${INSTALL_DIR}" ]]; then
  echo "Install directory not found: ${INSTALL_DIR}"
  echo "Run install.sh first."
  exit 1
fi

echo "[1/6] Updating application files..."
install_if_needed 0644 "${SCRIPT_DIR}/oxygen_monitor.py" "${INSTALL_DIR}/oxygen_monitor.py"
install_if_needed 0644 "${SCRIPT_DIR}/requirements.txt" "${INSTALL_DIR}/requirements.txt"
install_if_needed 0755 "${SCRIPT_DIR}/update.sh" "${INSTALL_DIR}/update.sh"
install_if_needed 0644 "${SCRIPT_DIR}/oxygen-monitor.service" "${INSTALL_DIR}/oxygen-monitor.service.template"
install_if_needed 0644 "${SCRIPT_DIR}/oxygen-monitor.conf" "${CONFIG_EXAMPLE_PATH}"

if [[ ! -f "${CONFIG_PATH}" ]]; then
  install -m 0644 "${SCRIPT_DIR}/oxygen-monitor.conf" "${CONFIG_PATH}"
fi

echo "[2/6] Updating Python dependencies..."
"${INSTALL_DIR}/.venv/bin/pip" install --upgrade pip
"${INSTALL_DIR}/.venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

echo "[3/6] Rebuilding systemd service..."
sed \
  -e "s|__APP_USER__|${APP_USER}|g" \
  -e "s|__APP_GROUP__|${APP_GROUP}|g" \
  -e "s|__INSTALL_DIR__|${INSTALL_DIR}|g" \
  "${SCRIPT_DIR}/oxygen-monitor.service" > "${SERVICE_PATH}"

echo "[4/6] Fixing ownership and permissions..."
chown -R "${APP_USER}:${APP_GROUP}" "${INSTALL_DIR}"
chmod 0644 "${SERVICE_PATH}"

echo "[5/6] Reloading systemd..."
systemctl daemon-reload

echo "[6/6] Restarting service..."
systemctl restart "${SERVICE_NAME}"

echo
echo "Update completed."
systemctl --no-pager --full status "${SERVICE_NAME}" || true
echo
echo "Live logs:"
echo "  sudo journalctl -u ${SERVICE_NAME} -f"
