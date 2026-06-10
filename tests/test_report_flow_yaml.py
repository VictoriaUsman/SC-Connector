"""Structural regression tests for workflows/report_flow.yaml.

Covers reviewer feedback on CU-868jxen7m:
  * A 403 must NOT be auto-retried (it is a permissions error, not transient).
  * The global error handler must surface a clean message, not a raw dump of
    the whole HTTP error object (status code + headers) via json.encode(e).
"""

from __future__ import annotations

from pathlib import Path

import yaml

_YAML_PATH = Path(__file__).resolve().parent.parent / "workflows" / "report_flow.yaml"


def _load() -> dict:
    return yaml.safe_load(_YAML_PATH.read_text())


def _steps_to_map(steps: list[dict]) -> dict:
    """Workflow steps are a list of single-key dicts; flatten to name->body."""
    out: dict = {}
    for step in steps:
        for name, body in step.items():
            out[name] = body
    return out


class TestNoRetryOn403:
    def test_non_throttle_retry_does_not_retry_403(self):
        doc = _load()
        steps = _steps_to_map(doc["non_throttle_retry"]["steps"])
        switch = steps["check_code"]["switch"]

        cases = {c["condition"]: c.get("return") for c in switch}
        # The 403 branch must return False (do not retry).
        forbidden = [v for cond, v in cases.items() if "403" in cond]
        assert forbidden == [False], f"expected 403 -> no retry, got {cases}"

        # Sanity: transient 5xx still retries.
        server_err = [v for cond, v in cases.items() if "500" in cond]
        assert server_err == [True]


class TestCleanErrorMessage:
    def test_clean_error_subworkflow_exists(self):
        doc = _load()
        assert "clean_error" in doc
        assert doc["clean_error"]["params"] == ["e"]

    def test_global_handler_uses_clean_message_not_raw_dump(self):
        doc = _load()
        main_steps = _steps_to_map(doc["main"]["steps"])
        except_steps = _steps_to_map(main_steps["execute_pipeline"]["except"]["steps"])
        mark_failed = except_steps["mark_job_failed"]

        # Find the branch that patches Firestore (the catch-all condition).
        patch_branch = next(
            c for c in mark_failed["switch"] if "steps" in c
        )
        branch_steps = _steps_to_map(patch_branch["steps"])

        # The clean_error helper must be invoked to build the user message.
        assert "build_user_message" in branch_steps
        assert branch_steps["build_user_message"]["call"] == "clean_error"

        # The user-facing error message must be the cleaned message, never the
        # raw json.encode(e) dump of the whole HTTP error object.
        msg_field = (
            branch_steps["patch_job_failed"]["args"]["body"]["fields"]
            ["error_details"]["mapValue"]["fields"]["message"]["stringValue"]
        )
        assert "user_message" in msg_field
        assert "json.encode" not in msg_field
