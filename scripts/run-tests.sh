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
    python -m pytest tests/ -v --tb=short
    ;;

  test-fn|function)
    FUNCTION_NAME="${1:-}"
    if [[ -z "$FUNCTION_NAME" ]]; then
      error "Usage: make test-fn NAME=<function_name>"
      exit 1
    fi

    # Tests live in the top-level tests/ directory, not colocated under
    # functions/ — this is a best-effort exact-name match (test_<name>.py),
    # not a guaranteed 1:1 mapping: several functions (create_report,
    # poll_status, download_upload, fetch_api) are covered indirectly by
    # differently-named test files instead of a dedicated test_<name>.py.
    TEST_FILE="$PROJECT_ROOT/tests/test_$FUNCTION_NAME.py"
    if [[ ! -f "$TEST_FILE" ]]; then
      error "No tests/test_$FUNCTION_NAME.py found."
      echo "This function may be covered by a differently-named test file instead."
      echo "Try: python -m pytest tests/ -k $FUNCTION_NAME -v"
      exit 1
    fi

    log "Running tests for function '$FUNCTION_NAME'..."
    echo ""
    python -m pytest "$TEST_FILE" -v --tb=short
    ;;

  integration)
    log "Running integration tests..."
    echo ""
    python -m pytest tests/ -v --tb=short -m integration
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
