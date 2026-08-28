#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILE="$SCRIPT_DIR/local-secrets.json"

# On Windows, a bare `python3` on PATH can be a non-functional Windows Store
# shim that prints an install prompt instead of running. Pick the first
# interpreter that actually executes.
PY=python3
"$PY" --version >/dev/null 2>&1 || PY=python
"$PY" --version >/dev/null 2>&1 || PY=py

if [[ ! -f "$FILE" ]]; then
  echo "{}" > "$FILE"
fi

ACTION="${1:-}"
NAME="${2:-}"
VALUE="${3:-}"

case "$ACTION" in
  set)
    if [[ -z "$NAME" || -z "$VALUE" ]]; then
      echo "Usage: make local-secret-set NAME=x VALUE=y" >&2
      exit 1
    fi
    "$PY" - "$FILE" "$NAME" "$VALUE" <<'PYEOF'
import json, sys
file, name, value = sys.argv[1], sys.argv[2], sys.argv[3]
with open(file) as f:
    data = json.load(f)
data[name] = value
with open(file, "w") as f:
    json.dump(data, f, indent=2)
print(f"Set local secret '{name}'")
PYEOF
    ;;
  get)
    "$PY" - "$FILE" "$NAME" <<'PYEOF'
import json, sys
file, name = sys.argv[1], sys.argv[2]
with open(file) as f:
    data = json.load(f)
if name not in data:
    print(f"'{name}' not found", file=sys.stderr)
    sys.exit(1)
print(data[name])
PYEOF
    ;;
  list)
    "$PY" - "$FILE" <<'PYEOF'
import json, sys
file = sys.argv[1]
with open(file) as f:
    data = json.load(f)
for k in sorted(data):
    print(k)
PYEOF
    ;;
  *)
    echo "Usage: local-secret-manager.sh {set|get|list} NAME [VALUE]" >&2
    exit 1
    ;;
esac
