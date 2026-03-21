#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_common.sh"

PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

COMMAND="${1:-}"
shift || true

cd "$PROJECT_ROOT"

# Load staging env for tests
ENV_FILE="$PROJECT_ROOT/.env.staging"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

export PYTHONPATH="$PROJECT_ROOT/functions:${PYTHONPATH:-}"

case "$COMMAND" in
  all)
    log "Running all tests..."
    echo ""
    python -m pytest functions/ -v --tb=short
    ;;

  test-fn|function)
    FUNCTION_NAME="${1:-}"
    if [[ -z "$FUNCTION_NAME" ]]; then
      error "Usage: make test-fn NAME=<function_name>"
      exit 1
    fi

    TEST_DIR="$PROJECT_ROOT/functions/$FUNCTION_NAME"
    if [[ ! -d "$TEST_DIR" ]]; then
      error "Function directory not found: $TEST_DIR"
      exit 1
    fi

    log "Running tests for function '$FUNCTION_NAME'..."
    echo ""
    python -m pytest "$TEST_DIR" -v --tb=short
    ;;

  integration)
    log "Running integration tests..."
    echo ""
    python -m pytest functions/ -v --tb=short -m integration
    ;;

  *)
    error "Unknown command '$COMMAND'."
    echo "Usage:"
    echo "  make test                          Run all tests"
    echo "  make test-fn NAME=<function_name>  Test a specific function"
    echo "  make test-integration              Run integration tests"
    exit 1
    ;;
esac

echo ""
log "Tests complete."
