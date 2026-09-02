"""Regression tests for service-account IAM role definitions.

Covers §3's Pulumi follow-up: the workflow SA no longer touches Firestore
(report_flow.yaml is fully off it — see the completed §3 plan), so
roles/datastore.user must be gone from its role list, and it must instead
be scoped to read exactly the new Supabase service-role key secret via a
dedicated SecretIamMember binding — not a project-wide role.

ast-parsed rather than imported, matching tests/test_functions_infra.py
(pulumi/pulumi_gcp are not installed in the test environment).
"""

from __future__ import annotations

import ast
from pathlib import Path

_IAM_PY = Path(__file__).resolve().parent.parent / "infra" / "resources" / "iam.py"


def _role_lists() -> list[list[str]]:
    """Every string-literal-only list in the module whose entries all look
    like IAM role names. The workflow SA's and functions SA's role lists
    are the only such lists in this file."""
    tree = ast.parse(_IAM_PY.read_text())
    lists = []
    for node in ast.walk(tree):
        if isinstance(node, ast.List) and node.elts:
            values = [
                e.value for e in node.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
            ]
            if len(values) == len(node.elts) and all(v.startswith("roles/") for v in values):
                lists.append(values)
    return lists


def _workflow_sa_roles() -> list[str]:
    """The workflow SA's role list is the one that never grants a
    functions-only role like bigquery.dataEditor."""
    candidates = [r for r in _role_lists() if "roles/bigquery.dataEditor" not in r]
    assert len(candidates) == 1, f"expected exactly one workflow-SA role list, found {candidates}"
    return candidates[0]


def _find_calls(func_suffix: str) -> list[ast.Call]:
    tree = ast.parse(_IAM_PY.read_text())
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == func_suffix
    ]


class TestWorkflowSaDropsDatastoreUser:
    def test_datastore_user_role_removed(self):
        assert "roles/datastore.user" not in _workflow_sa_roles()

    def test_logging_role_still_present(self):
        assert "roles/logging.logWriter" in _workflow_sa_roles()


class TestWorkflowSaGetsScopedSecretAccess:
    def test_secret_iam_member_grants_accessor_role(self):
        calls = _find_calls("SecretIamMember")
        assert len(calls) == 1, f"expected exactly one SecretIamMember binding, found {len(calls)}"
        kwargs = {kw.arg: kw.value for kw in calls[0].keywords}
        assert "role" in kwargs
        assert isinstance(kwargs["role"], ast.Constant)
        assert kwargs["role"].value == "roles/secretmanager.secretAccessor"

    def test_secret_iam_member_scoped_to_a_secret_not_the_project(self):
        """Must bind via secret_id (resource-scoped SecretIamMember), never
        a project-wide gcp.projects.IAMMember for this role."""
        calls = _find_calls("SecretIamMember")
        kwargs = {kw.arg for kw in calls[0].keywords}
        assert "secret_id" in kwargs

    def test_bind_workflow_secret_access_function_exists(self):
        tree = ast.parse(_IAM_PY.read_text())
        names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        assert "bind_workflow_secret_access" in names
