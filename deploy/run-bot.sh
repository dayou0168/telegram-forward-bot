#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <bot-name> [up|down|restart|logs|ps|build]"
  echo "Example: $0 notice-bot up"
  exit 1
fi

BOT_NAME="$1"
ACTION="${2:-up}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/deploy/envs/$BOT_NAME.env"
PROJECT_NAME="tg_forward_${BOT_NAME//[^a-zA-Z0-9]/_}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing env file: $ENV_FILE"
  echo "Create it from deploy/envs/example-bot.env first."
  exit 1
fi

cd "$ROOT_DIR"

case "$ACTION" in
  up)
    BOT_ENV_FILE="$ENV_FILE" docker compose -p "$PROJECT_NAME" up -d --build
    ;;
  build)
    BOT_ENV_FILE="$ENV_FILE" docker compose -p "$PROJECT_NAME" build
    ;;
  down)
    BOT_ENV_FILE="$ENV_FILE" docker compose -p "$PROJECT_NAME" down
    ;;
  restart)
    BOT_ENV_FILE="$ENV_FILE" docker compose -p "$PROJECT_NAME" restart
    ;;
  logs)
    BOT_ENV_FILE="$ENV_FILE" docker compose -p "$PROJECT_NAME" logs -f
    ;;
  ps)
    BOT_ENV_FILE="$ENV_FILE" docker compose -p "$PROJECT_NAME" ps
    ;;
  *)
    echo "Unknown action: $ACTION"
    echo "Allowed: up, down, restart, logs, ps, build"
    exit 1
    ;;
esac
