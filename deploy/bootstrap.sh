#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  curl -fsSL RAW_URL | sudo bash -s -- --mode docker --bot-name notice-bot --bot-token TOKEN --owner-user-ids 123456789

Options:
  --mode native|docker       Install mode. Default: docker
  --bot-name NAME            Bot instance name. Example: notice-bot
  --bot-token TOKEN          Telegram BotFather token. Optional when env file already exists.
  --owner-user-ids IDS       Telegram owner UID list, comma separated. Optional when env file already exists.
  --install-dir DIR          Install directory. Default: /opt/tg-forward-bots/telegram-forward-bot
  --repo OWNER/REPO          GitHub repository. Default: dayou0168/telegram-forward-bot
  --ref REF                  Branch or tag to deploy. Default: main
  --github-token TOKEN       GitHub token for private repositories. Can also use GITHUB_TOKEN or GH_TOKEN env.
  --service-user USER        Native mode only. Linux system user for the bot. Default: tgforward
  --skip-system-upgrade      Run apt update/install, but skip apt upgrade in the installer.
  --no-start                 Install everything, but do not start the bot.
  -h, --help                 Show this help.
USAGE
}

MODE="${MODE:-docker}"
BOT_NAME="${BOT_NAME:-}"
BOT_TOKEN="${BOT_TOKEN:-}"
OWNER_USER_IDS="${OWNER_USER_IDS:-}"
INSTALL_DIR="${INSTALL_DIR:-/opt/tg-forward-bots/telegram-forward-bot}"
GITHUB_REPO="${GITHUB_REPO:-dayou0168/telegram-forward-bot}"
GITHUB_REF="${GITHUB_REF:-main}"
GITHUB_TOKEN="${GITHUB_TOKEN:-${GH_TOKEN:-}}"
SERVICE_USER="${SERVICE_USER:-}"
SKIP_SYSTEM_UPGRADE=false
NO_START=false

require_root() {
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "Please run this bootstrap with sudo/root, for example:"
    echo "  curl -fsSL RAW_URL | sudo bash -s -- --mode docker --bot-name notice-bot"
    exit 1
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      MODE="${2:-}"
      shift 2
      ;;
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
    --install-dir)
      INSTALL_DIR="${2:-}"
      shift 2
      ;;
    --repo)
      GITHUB_REPO="${2:-}"
      shift 2
      ;;
    --ref)
      GITHUB_REF="${2:-}"
      shift 2
      ;;
    --github-token)
      GITHUB_TOKEN="${2:-}"
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
      NO_START=true
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

if [[ "$MODE" != "native" && "$MODE" != "docker" ]]; then
  echo "Invalid --mode: $MODE. Allowed: native, docker"
  exit 1
fi

if [[ -z "$BOT_NAME" ]]; then
  echo "--bot-name is required."
  usage
  exit 1
fi

if [[ ! "$GITHUB_REPO" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
  echo "Invalid --repo. Expected OWNER/REPO."
  exit 1
fi

if [[ "$INSTALL_DIR" =~ [[:space:]] ]]; then
  echo "Install path contains spaces, which is not supported: $INSTALL_DIR"
  exit 1
fi

require_root

if [[ -f /etc/os-release ]]; then
  . /etc/os-release
fi

if [[ "${ID:-}" != "ubuntu" && "${ID:-}" != "debian" ]]; then
  echo "This bootstrap supports Ubuntu/Debian servers. Detected: ${PRETTY_NAME:-unknown}"
  exit 1
fi

echo "Installing bootstrap dependencies..."
apt-get update
env DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl git

ASKPASS_FILE=""
cleanup() {
  if [[ -n "$ASKPASS_FILE" && -f "$ASKPASS_FILE" ]]; then
    rm -f "$ASKPASS_FILE"
  fi
}
trap cleanup EXIT

prepare_git_auth() {
  export GIT_TERMINAL_PROMPT=0
  if [[ -z "$GITHUB_TOKEN" ]]; then
    return
  fi

  ASKPASS_FILE="$(mktemp)"
  chmod 700 "$ASKPASS_FILE"
  cat > "$ASKPASS_FILE" <<EOF
#!/usr/bin/env sh
case "\$1" in
  *Username*) printf '%s\n' 'x-access-token' ;;
  *Password*) printf '%s\n' '$GITHUB_TOKEN' ;;
  *) printf '%s\n' 'x-access-token' ;;
esac
EOF
  export GIT_ASKPASS="$ASKPASS_FILE"
}

prepare_git_auth

PARENT_DIR="$(dirname "$INSTALL_DIR")"
REPO_URL="https://github.com/${GITHUB_REPO}.git"

mkdir -p "$PARENT_DIR"

if [[ -d "$INSTALL_DIR/.git" ]]; then
  echo "Updating existing repository: $INSTALL_DIR"
  git -C "$INSTALL_DIR" remote set-url origin "$REPO_URL"
  git -C "$INSTALL_DIR" fetch origin "$GITHUB_REF"
  if ! git -C "$INSTALL_DIR" checkout "$GITHUB_REF" >/dev/null 2>&1; then
    git -C "$INSTALL_DIR" checkout -b "$GITHUB_REF" "origin/$GITHUB_REF"
  fi
  git -C "$INSTALL_DIR" pull --ff-only origin "$GITHUB_REF"
elif [[ -e "$INSTALL_DIR" ]]; then
  echo "Install directory already exists but is not a git repository: $INSTALL_DIR"
  echo "Move it away or choose another --install-dir."
  exit 1
else
  echo "Cloning repository: $GITHUB_REPO@$GITHUB_REF"
  if ! git clone --branch "$GITHUB_REF" "$REPO_URL" "$INSTALL_DIR"; then
    echo
    echo "Clone failed. If the repository is private, pass a GitHub token:"
    echo "  export GITHUB_TOKEN=your_token"
    echo "  curl -fsSL -H \"Authorization: Bearer \${GITHUB_TOKEN}\" RAW_URL | sudo GITHUB_TOKEN=\"\${GITHUB_TOKEN}\" bash -s -- ..."
    exit 1
  fi
fi

chmod +x "$INSTALL_DIR"/deploy/*.sh

INSTALL_SCRIPT="$INSTALL_DIR/deploy/install-${MODE}.sh"
if [[ ! -f "$INSTALL_SCRIPT" ]]; then
  echo "Missing installer: $INSTALL_SCRIPT"
  exit 1
fi

INSTALL_ARGS=(--bot-name "$BOT_NAME")
if [[ -n "$BOT_TOKEN" ]]; then
  INSTALL_ARGS+=(--bot-token "$BOT_TOKEN")
fi
if [[ -n "$OWNER_USER_IDS" ]]; then
  INSTALL_ARGS+=(--owner-user-ids "$OWNER_USER_IDS")
fi
if [[ "$SKIP_SYSTEM_UPGRADE" == "true" ]]; then
  INSTALL_ARGS+=(--skip-system-upgrade)
fi
if [[ "$NO_START" == "true" ]]; then
  INSTALL_ARGS+=(--no-start)
fi
if [[ "$MODE" == "native" && -n "$SERVICE_USER" ]]; then
  INSTALL_ARGS+=(--service-user "$SERVICE_USER")
fi

echo "Running installer: $INSTALL_SCRIPT"
bash "$INSTALL_SCRIPT" "${INSTALL_ARGS[@]}"
