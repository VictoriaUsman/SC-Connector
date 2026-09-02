"""Regression tests for the Cloud Workflow resource definition.

Covers §3's Pulumi follow-up: report_flow.yaml's get_supabase_key
subworkflow needs the Supabase secret's resource id substituted for its
__SUPABASE_SECRET_NAME__ placeholder at deploy time (the same mechanism
already used for the six __*_FUNCTION_URL__ placeholders), and SUPABASE_URL
needs to reach the workflow as a user-defined environment variable, the
same way GOOGLE_CLOUD_PROJECT_ID reaches it as a GCP-provided built-in one.

ast-parsed rather than imported, matching tests/test_functions_infra.py
(pulumi/pulumi_gcp are not installed in the test environment).
"""

from __future__ import annotations

import ast
from pathlib import Path

_WORKFLOW_PY = (
    Path(__file__).resolve().parent.parent / "infra" / "resources" / "workflow.py"
)


def _tree() -> ast.Module:
    return ast.parse(_WORKFLOW_PY.read_text())


def _create_function() -> ast.FunctionDef:
    for node in ast.walk(_tree()):
        if isinstance(node, ast.FunctionDef) and node.name == "create":
            return node
    raise AssertionError("create() not found in infra/resources/workflow.py")


class TestSupabaseSecretPlaceholderWired:
    def test_create_accepts_a_supabase_secret_param(self):
        fn = _create_function()
        arg_names = [a.arg for a in fn.args.args]
        assert "supabase_secret" in arg_names

    def test_template_replace_chain_includes_secret_name_placeholder(self):
        source = _WORKFLOW_PY.read_text()
        assert '.replace("__SUPABASE_SECRET_NAME__"' in source


class TestSupabaseUrlEnvVarWired:
    def test_workflow_resource_sets_user_env_vars(self):
        workflow_calls = [
            node for node in ast.walk(_tree())
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "Workflow"
        ]
        assert len(workflow_calls) == 1, f"expected exactly one Workflow(...) call, found {len(workflow_calls)}"
        kwargs = {kw.arg for kw in workflow_calls[0].keywords}
        assert "user_env_vars" in kwargs

    def test_supabase_url_read_from_kalilos_config(self):
        source = _WORKFLOW_PY.read_text()
        assert 'config.get("supabase-url")' in source
