#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <bot-name> [start|stop|restart|status|logs|enable|disable]"
  echo "Example: $0 notice-bot logs"
  exit 1
fi

BOT_NAME="$1"
ACTION="${2:-status}"
SERVICE_BOT_NAME="${BOT_NAME//[^a-zA-Z0-9]/_}"
SERVICE_NAME="tg-forward-${SERVICE_BOT_NAME}.service"

run_root() {
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

case "$ACTION" in
  start|stop|restart|enable|disable)
    run_root systemctl "$ACTION" "$SERVICE_NAME"
    ;;
  status)
    run_root systemctl --no-pager --full status "$SERVICE_NAME"
    ;;
  logs)
    run_root journalctl -u "$SERVICE_NAME" -f
    ;;
  *)
    echo "Unknown action: $ACTION"
    echo "Allowed: start, stop, restart, status, logs, enable, disable"
    exit 1
    ;;
esac
