"""Local-mode secret resolution — bypasses Secret Manager when LOCAL_MODE=true.

scripts/local-secrets.json (gitignored) holds {secret_name: raw_payload}
using the exact same secret names Secret Manager uses in staging/prod, so
switching between local and real secrets is just flipping LOCAL_MODE.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_LOCAL_SECRETS_FILE = Path(__file__).resolve().parents[2] / "scripts" / "local-secrets.json"

_local_secrets_cache: dict[str, str] | None = None


def is_local_mode() -> bool:
    return os.environ.get("LOCAL_MODE", "").lower() == "true"


def _load_local_secrets() -> dict[str, str]:
    global _local_secrets_cache
    if _local_secrets_cache is None:
        if not _LOCAL_SECRETS_FILE.exists():
            raise FileNotFoundError(
                f"LOCAL_MODE is set but {_LOCAL_SECRETS_FILE} does not exist. "
                f"Create it with `make local-secret-set NAME=<secret_name> VALUE=<value>`."
            )
        _local_secrets_cache = json.loads(_LOCAL_SECRETS_FILE.read_text())
    return _local_secrets_cache


def resolve_secret(secret_name: str) -> str:
    """Return a secret's raw payload string from scripts/local-secrets.json."""
    secrets = _load_local_secrets()
    if secret_name not in secrets:
        raise KeyError(
            f"'{secret_name}' not found in {_LOCAL_SECRETS_FILE}. "
            f"Add it with `make local-secret-set NAME={secret_name} VALUE=<value>`."
        )
    return secrets[secret_name]
