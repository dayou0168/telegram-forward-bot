#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  sudo bash deploy/install-docker.sh --bot-name notice-bot --bot-token TOKEN --owner-user-ids 123456789

Options:
  --bot-name NAME            Bot instance name. Example: notice-bot
  --bot-token TOKEN          Telegram BotFather token. Optional when env file already exists.
  --owner-user-ids IDS       Telegram owner UID list, comma separated. Optional when env file already exists.
  --skip-system-upgrade      Run apt update/install, but skip apt upgrade.
  --no-start                 Install Docker and env file, but do not start the bot.
  -h, --help                 Show this help.

The script installs Docker Engine and Docker Compose plugin when missing,
creates deploy/envs/<bot-name>.env when missing, then starts the bot stack.
USAGE
}

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOT_NAME="${BOT_NAME:-}"
BOT_TOKEN="${BOT_TOKEN:-}"
OWNER_USER_IDS="${OWNER_USER_IDS:-}"
SKIP_SYSTEM_UPGRADE=false
START_STACK=true

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
    if [[ -r /dev/tty ]]; then
      read -rsp "$prompt: " value < /dev/tty
    else
      read -rsp "$prompt: " value
    fi
    echo
  else
    if [[ -r /dev/tty ]]; then
      read -rp "$prompt: " value < /dev/tty
    else
      read -rp "$prompt: " value
    fi
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
    --skip-system-upgrade)
      SKIP_SYSTEM_UPGRADE=true
      shift
      ;;
    --no-start)
      START_STACK=false
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

ENV_DIR="$ROOT_DIR/deploy/envs"
ENV_FILE="$ENV_DIR/$BOT_NAME.env"
PROJECT_NAME="tg_forward_${BOT_NAME//[^a-zA-Z0-9]/_}"
CONTAINER_UID=10001
CONTAINER_GID=10001

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
  if [[ -n "${SUDO_USER:-}" && "${SUDO_USER:-}" != "root" ]]; then
    run_root chown "$SUDO_USER:$SUDO_USER" "$ENV_FILE" || true
  fi
  echo "Created env file: $ENV_FILE"
fi

if [[ -f /etc/os-release ]]; then
  . /etc/os-release
fi

if [[ "${ID:-}" != "ubuntu" && "${ID:-}" != "debian" ]]; then
  echo "This installer supports Ubuntu/Debian servers. Detected: ${PRETTY_NAME:-unknown}"
  exit 1
fi

CODENAME="${VERSION_CODENAME:-}"
if [[ -z "$CODENAME" ]] && command -v lsb_release >/dev/null 2>&1; then
  CODENAME="$(lsb_release -cs)"
fi
if [[ -z "$CODENAME" ]]; then
  echo "Could not detect OS codename for Docker repository."
  exit 1
fi

echo "Updating apt package metadata..."
run_root apt-get update

if [[ "$SKIP_SYSTEM_UPGRADE" != "true" ]]; then
  echo "Upgrading installed system packages..."
  run_root env DEBIAN_FRONTEND=noninteractive apt-get -y upgrade
fi

echo "Installing Docker repository dependencies..."
run_root env DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ca-certificates \
  curl \
  gnupg

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  echo "Installing Docker Engine and Compose plugin..."
  tmp_gpg="$(mktemp)"
  curl -fsSL "https://download.docker.com/linux/${ID}/gpg" -o "$tmp_gpg"
  run_root install -m 0755 -d /etc/apt/keyrings
  run_root install -m 0644 "$tmp_gpg" /etc/apt/keyrings/docker.asc
  rm -f "$tmp_gpg"

  repo_line="deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/${ID} ${CODENAME} stable"
  printf '%s\n' "$repo_line" | run_root tee /etc/apt/sources.list.d/docker.list >/dev/null

  run_root apt-get update
  run_root env DEBIAN_FRONTEND=noninteractive apt-get install -y \
    docker-ce \
    docker-ce-cli \
    containerd.io \
    docker-buildx-plugin \
    docker-compose-plugin
fi

run_root systemctl enable --now docker
run_root chmod +x "$ROOT_DIR/deploy/run-bot.sh"
run_root mkdir -p "$ROOT_DIR/data"
run_root chown -R "$CONTAINER_UID:$CONTAINER_GID" "$ROOT_DIR/data"
run_root chmod 750 "$ROOT_DIR/data"

if [[ -n "${SUDO_USER:-}" && "${SUDO_USER:-}" != "root" ]]; then
  run_root usermod -aG docker "$SUDO_USER" || true
fi

if [[ "$START_STACK" == "true" ]]; then
  echo "Starting Docker Compose project: $PROJECT_NAME"
  cd "$ROOT_DIR"
  run_root env BOT_ENV_FILE="$ENV_FILE" docker compose -p "$PROJECT_NAME" up -d --build
  run_root env BOT_ENV_FILE="$ENV_FILE" docker compose -p "$PROJECT_NAME" ps
fi

cat <<EOF

Docker Compose install complete.

Manage this bot:
  ./deploy/run-bot.sh $BOT_NAME ps
  ./deploy/run-bot.sh $BOT_NAME logs
  ./deploy/run-bot.sh $BOT_NAME restart

If your user was just added to the docker group, log out and log back in before
running docker commands without sudo.
EOF
