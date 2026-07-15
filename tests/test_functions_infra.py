"""Regression tests for the Cloud Function infra definitions.

Covers CU-868k9yg8r: the ``download-upload`` function saturated its
``max_instances`` ceiling during the scheduler's nightly fan-out, so the
platform shed the excess invocations with 429 "Rate exceeded." and the
report-pull workflow failed. The ceiling must stay raised so bursts of
concurrent downloads are served instead of throttled.

The infra module imports ``pulumi`` at module scope, which is not installed in
the test environment (CI only installs each function's requirements). To keep
this test dependency-free we parse ``FUNCTION_DEFS`` out of the source with
``ast`` instead of importing the module.
"""

from __future__ import annotations

import ast
from pathlib import Path

_FUNCTIONS_PY = (
    Path(__file__).resolve().parent.parent / "infra" / "resources" / "functions.py"
)


def _load_function_defs() -> list[dict]:
    """Extract the ``FUNCTION_DEFS`` list literal without importing pulumi."""
    tree = ast.parse(_FUNCTIONS_PY.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "FUNCTION_DEFS":
                    return ast.literal_eval(node.value)
    raise AssertionError("FUNCTION_DEFS not found in infra/resources/functions.py")


def _by_name() -> dict[str, dict]:
    return {fn["name"]: fn for fn in _load_function_defs()}


class TestDownloadUploadInstanceCeiling:
    def test_download_upload_ceiling_raised(self):
        """The ceiling must be well above the old value of 10 so a fan-out
        burst of concurrent Ads downloads does not saturate the service."""
        download = _by_name()["download-upload"]
        assert download["max_instances"] >= 30, (
            "download-upload max_instances too low; a fan-out burst will "
            f"saturate the service and 429: {download['max_instances']}"
        )

    def test_download_upload_ceiling_exceeds_create_report(self):
        """download-upload holds an instance far longer than create-report, so
        it must scale at least as wide to absorb the same fan-out burst."""
        defs = _by_name()
        assert (
            defs["download-upload"]["max_instances"]
            >= defs["create-report"]["max_instances"]
        )
