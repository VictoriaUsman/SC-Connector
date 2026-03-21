#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_common.sh"

PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

FUNCTION_NAME="${1:-}"
PORT="${2:-8080}"

if [[ -z "$FUNCTION_NAME" ]]; then
  error "Usage: make local-fn NAME=<function_name> [PORT=8080]"
  exit 1
fi

FUNCTION_DIR="$PROJECT_ROOT/functions/$FUNCTION_NAME"

if [[ ! -d "$FUNCTION_DIR" ]]; then
  error "Function directory not found: $FUNCTION_DIR"
  echo "Available functions:"
  ls -1 "$PROJECT_ROOT/functions/" | grep -v shared | grep -v __pycache__
  exit 1
fi

# Load staging env for local development
ENV_FILE="$PROJECT_ROOT/.env.staging"
if [[ -f "$ENV_FILE" ]]; then
  log "Loading environment from $ENV_FILE"
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

log "Starting '$FUNCTION_NAME' locally on port $PORT..."
log "Function directory: $FUNCTION_DIR"
echo ""

cd "$FUNCTION_DIR"

# Install function-specific dependencies
if [[ -f "$FUNCTION_DIR/requirements.txt" ]]; then
  pip install -q -r "$FUNCTION_DIR/requirements.txt" 2>/dev/null || true
fi

# Allow `from shared.xxx import ...` to resolve
export PYTHONPATH="$PROJECT_ROOT/functions:${PYTHONPATH:-}"

functions-framework \
  --target=handler \
  --port="$PORT" \
  --debug
