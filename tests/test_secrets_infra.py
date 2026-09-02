"""Regression tests for the Secret Manager shell definitions.

Covers §3's Pulumi follow-up: report_flow.yaml's get_supabase_key
subworkflow expects a kalilos-{env}-supabase-service-key secret to exist —
this test guards that the shell for it is actually provisioned.

The infra module imports ``pulumi``/``pulumi_gcp``, which are not installed
in the test environment (each function's own venv/requirements installs
them at actual deploy time only). To keep this test dependency-free we
parse ``SECRET_SHELLS`` out of the source with ``ast`` instead of importing
the module (matches tests/test_functions_infra.py's approach).
"""

from __future__ import annotations

import ast
from pathlib import Path

_SECRETS_PY = (
    Path(__file__).resolve().parent.parent / "infra" / "resources" / "secrets.py"
)


def _load_secret_shells() -> list[str]:
    tree = ast.parse(_SECRETS_PY.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "SECRET_SHELLS":
                    return ast.literal_eval(node.value)
    raise AssertionError("SECRET_SHELLS not found in infra/resources/secrets.py")


class TestSupabaseServiceKeyShell:
    def test_supabase_service_key_shell_provisioned(self):
        """report_flow.yaml's get_supabase_key subworkflow reads
        kalilos-{env}-supabase-service-key — the shell must exist so
        `make secret-set` can populate the real value."""
        shells = _load_secret_shells()
        assert "supabase-service-key" in shells

    def test_existing_shells_untouched(self):
        shells = _load_secret_shells()
        assert "sp-api-app-credentials" in shells
        assert "ads-api-app-credentials" in shells
