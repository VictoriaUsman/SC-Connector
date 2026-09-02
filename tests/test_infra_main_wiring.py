"""Regression tests for infra/__main__.py's Supabase-secret wiring.

Covers §3's Pulumi follow-up: the secret shell (secrets.py), the workflow
resource (workflow.py), and the scoped IAM binding (iam.py) all need to be
connected together in the entry point, or the whole chain is dead code.

ast-parsed rather than imported — infra/__main__.py runs pulumi.get_stack()
at module scope, which fails outside a real Pulumi context, and
pulumi/pulumi_gcp are not installed in the test environment anyway
(matches tests/test_functions_infra.py's approach).
"""

from __future__ import annotations

import ast
import py_compile
import tempfile
from pathlib import Path

_INFRA_DIR = Path(__file__).resolve().parent.parent / "infra"
_MAIN_PY = _INFRA_DIR / "__main__.py"


def _calls_named(func_name: str) -> list[ast.Call]:
    tree = ast.parse(_MAIN_PY.read_text())
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == func_name
    ]


class TestSupabaseSecretWiring:
    def test_workflow_create_receives_the_supabase_secret(self):
        """workflow.create(...) specifically (not any other .create(...)
        call in this file) must receive secret_resources["supabase-service-key"]."""
        calls = _calls_named("create")
        matches = [
            c for c in calls
            if isinstance(c.func, ast.Attribute)
            and isinstance(c.func.value, ast.Name)
            and c.func.value.id == "workflow"
            and any(
                isinstance(arg, ast.Subscript)
                and isinstance(arg.value, ast.Name)
                and arg.value.id == "secret_resources"
                and isinstance(arg.slice, ast.Constant)
                and arg.slice.value == "supabase-service-key"
                for arg in c.args
            )
        ]
        assert matches, 'workflow.create(...) must receive secret_resources["supabase-service-key"]'

    def test_bind_workflow_secret_access_receives_the_same_secret(self):
        """bind_workflow_secret_access(...) must be called with
        secret_resources["supabase-service-key"] — the SAME key
        workflow.create(...) receives, not a different secret."""
        calls = _calls_named("bind_workflow_secret_access")
        assert len(calls) == 1
        matches = [
            arg for arg in calls[0].args
            if isinstance(arg, ast.Subscript)
            and isinstance(arg.value, ast.Name)
            and arg.value.id == "secret_resources"
            and isinstance(arg.slice, ast.Constant)
            and arg.slice.value == "supabase-service-key"
        ]
        assert matches, 'bind_workflow_secret_access(...) must receive secret_resources["supabase-service-key"]'


class TestTouchedInfraFilesCompile:
    """Cheap syntax-only sanity net (no pulumi/pulumi_gcp import needed) for
    the four files this plan's tasks touch."""

    def test_all_touched_infra_files_compile(self):
        for rel in (
            "__main__.py",
            "resources/secrets.py",
            "resources/iam.py",
            "resources/workflow.py",
        ):
            path = _INFRA_DIR / rel
            with tempfile.TemporaryDirectory() as tmp:
                py_compile.compile(str(path), cfile=str(Path(tmp) / "out.pyc"), doraise=True)
