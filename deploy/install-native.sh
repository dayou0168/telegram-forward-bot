#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  sudo bash deploy/install-native.sh --bot-name notice-bot --bot-token TOKEN --owner-user-ids 123456789

Options:
  --bot-name NAME            Bot instance name. Example: notice-bot
  --bot-token TOKEN          Telegram BotFather token. Optional when env file already exists.
  --owner-user-ids IDS       Telegram owner UID list, comma separated. Optional when env file already exists.
  --service-user USER        Linux system user for the bot. Default: tgforward
  --skip-system-upgrade      Run apt update/install, but skip apt upgrade.
  --no-start                 Install dependencies and systemd unit, but do not start the service.
  -h, --help                 Show this help.

The script creates deploy/envs/<bot-name>.env when missing, installs Python deps
into .venv, and creates a systemd service named tg-forward-<bot-name>.service.
USAGE
}

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOT_NAME="${BOT_NAME:-}"
BOT_TOKEN="${BOT_TOKEN:-}"
OWNER_USER_IDS="${OWNER_USER_IDS:-}"
SERVICE_USER="${SERVICE_USER:-tgforward}"
SKIP_SYSTEM_UPGRADE=false
START_SERVICE=true

run_root() {
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

require_sudo() {
  if [[ "${EUID:-$(id -u)}" -ne 0 ]] && ! command -v sudo >/dev/null 2>&1; then
    echo "This script needs root permissions, but sudo is not installed."
    exit 1
  fi
}

prompt_if_empty() {
  local var_name="$1"
  local prompt="$2"
  local secret="${3:-false}"
  local value="${!var_name:-}"
  if [[ -n "$value" ]]; then
    return
  fi
  if [[ "$secret" == "true" ]]; then
    read -rsp "$prompt: " value
    echo
  else
    read -rp "$prompt: " value
  fi
  printf -v "$var_name" '%s' "$value"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bot-name)
      BOT_NAME="${2:-}"
      shift 2
      ;;
    --bot-token)
      BOT_TOKEN="${2:-}"
      shift 2
      ;;
    --owner-user-ids)
      OWNER_USER_IDS="${2:-}"
      shift 2
      ;;
    --service-user)
      SERVICE_USER="${2:-}"
      shift 2
      ;;
    --skip-system-upgrade)
      SKIP_SYSTEM_UPGRADE=true
      shift
      ;;
    --no-start)
      START_SERVICE=false
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      usage
      exit 1
      ;;
  esac
done

require_sudo
prompt_if_empty BOT_NAME "Bot instance name, for example notice-bot"

if [[ ! "$BOT_NAME" =~ ^[a-zA-Z0-9._-]+$ ]]; then
  echo "Invalid bot name. Use only letters, numbers, dot, underscore, or hyphen."
  exit 1
fi

if [[ "$ROOT_DIR" =~ [[:space:]] ]]; then
  echo "The project path contains spaces, which is not supported for systemd mode:"
  echo "$ROOT_DIR"
  echo "Move the project to a path like /opt/tg-forward-bots/telegram-forward-bot first."
  exit 1
fi

ENV_DIR="$ROOT_DIR/deploy/envs"
ENV_FILE="$ENV_DIR/$BOT_NAME.env"
DATA_DIR="$ROOT_DIR/data"
SERVICE_BOT_NAME="${BOT_NAME//[^a-zA-Z0-9]/_}"
SERVICE_NAME="tg-forward-${SERVICE_BOT_NAME}.service"
SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME"

if [[ -f "$ENV_FILE" ]]; then
  echo "Using existing env file: $ENV_FILE"
else
  prompt_if_empty BOT_TOKEN "Telegram bot token" true
  prompt_if_empty OWNER_USER_IDS "Owner Telegram UID list, comma separated"
  if [[ -z "$BOT_TOKEN" || -z "$OWNER_USER_IDS" ]]; then
    echo "BOT_TOKEN and OWNER_USER_IDS are required when env file does not exist."
    exit 1
  fi
  tmp_env="$(mktemp)"
  {
    printf 'BOT_TOKEN=%s\n' "$BOT_TOKEN"
    printf 'OWNER_USER_IDS=%s\n' "$OWNER_USER_IDS"
    printf 'DATABASE_URL=sqlite+aiosqlite:///./data/%s.db\n' "$BOT_NAME"
    printf 'UNAUTHORIZED_REPLY=true\n'
    printf 'SEND_DELAY_SECONDS=0.08\n'
  } > "$tmp_env"
  run_root mkdir -p "$ENV_DIR"
  run_root install -m 0600 "$tmp_env" "$ENV_FILE"
  rm -f "$tmp_env"
  echo "Created env file: $ENV_FILE"
fi

if [[ -f /etc/os-release ]]; then
  . /etc/os-release
fi

if [[ "${ID:-}" != "ubuntu" && "${ID:-}" != "debian" ]]; then
  echo "This installer supports Ubuntu/Debian servers. Detected: ${PRETTY_NAME:-unknown}"
  exit 1
fi

echo "Updating apt package metadata..."
run_root apt-get update

if [[ "$SKIP_SYSTEM_UPGRADE" != "true" ]]; then
  echo "Upgrading installed system packages..."
  run_root env DEBIAN_FRONTEND=noninteractive apt-get -y upgrade
fi

echo "Installing native dependencies..."
run_root env DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ca-certificates \
  curl \
  python3 \
  python3-pip \
  python3-venv

if ! getent group "$SERVICE_USER" >/dev/null 2>&1; then
  echo "Creating system group: $SERVICE_USER"
  run_root groupadd --system "$SERVICE_USER"
fi

if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  echo "Creating system user: $SERVICE_USER"
  run_root useradd --system --gid "$SERVICE_USER" --home "$ROOT_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

run_root mkdir -p "$DATA_DIR"
run_root chown "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR"
run_root chmod 750 "$DATA_DIR"

echo "Installing Python dependencies into .venv..."
run_root python3 -m venv "$ROOT_DIR/.venv"
run_root "$ROOT_DIR/.venv/bin/python" -m pip install --upgrade pip
run_root "$ROOT_DIR/.venv/bin/pip" install --upgrade -r "$ROOT_DIR/requirements.txt"

echo "Writing systemd unit: $SERVICE_FILE"
run_root tee "$SERVICE_FILE" >/dev/null <<EOF
[Unit]
Description=Telegram Forward Bot ($BOT_NAME)
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$ROOT_DIR
EnvironmentFile=$ENV_FILE
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=$ROOT_DIR/.venv/bin/python -m bot.main
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=$DATA_DIR
UMask=0077

[Install]
WantedBy=multi-user.target
EOF

run_root systemctl daemon-reload
run_root systemctl enable "$SERVICE_NAME"

if [[ "$START_SERVICE" == "true" ]]; then
  echo "Starting service: $SERVICE_NAME"
  run_root systemctl restart "$SERVICE_NAME"
  run_root systemctl --no-pager --full status "$SERVICE_NAME" || true
fi

cat <<EOF

Native install complete.

Manage this bot:
  sudo systemctl status $SERVICE_NAME
  sudo systemctl restart $SERVICE_NAME
  sudo journalctl -u $SERVICE_NAME -f

Or use:
  bash deploy/run-native-bot.sh $BOT_NAME logs
EOF
