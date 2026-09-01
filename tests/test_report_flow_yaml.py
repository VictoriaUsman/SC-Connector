"""Structural regression tests for workflows/report_flow.yaml.

Covers reviewer feedback on CU-868jxen7m:
  * A 403 must NOT be auto-retried (it is a permissions error, not transient).
  * The global error handler must surface a clean message, not a raw dump of
    the whole HTTP error object (status code + headers) via json.encode(e).

Covers CU-868k0buex:
  * Every error-handling expression must read the HTTP status via
    ``map.get(..., "code")`` (not a bare ``${err.code}``) so a code-less
    transport error (Connection reset / timeout, as surfaced by the SB
    campaigns legacy v2 augmentation) does not raise
    "KeyError: key not found: code".

Covers CU-868k9yg8r:
  * The download-upload (and fetch_api) steps must carry a throttle-retry
    budget large enough to ride out a platform 429 "Rate exceeded." burst.
    During the scheduler's nightly fan-out the download-upload service
    saturates and Google Frontend sheds the excess invocations with 429; a
    5-retry budget was exhausted before the service drained, crashing the
    workflow. The budget must stay >= 10 so the pull self-heals.
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

        # Find the branch that patches the job (the catch-all condition).
        patch_branch = next(
            c for c in mark_failed["switch"] if "steps" in c
        )
        branch_steps = _steps_to_map(patch_branch["steps"])

        # The clean_error helper must be invoked to build the user message.
        assert "build_user_message" in branch_steps
        assert branch_steps["build_user_message"]["call"] == "clean_error"

        # The user-facing error message must be the cleaned message, never the
        # raw json.encode(e) dump of the whole HTTP error object.
        msg_field = branch_steps["patch_job_failed"]["args"]["body"]["error_details"]["message"]
        assert "user_message" in msg_field
        assert "json.encode" not in msg_field


class TestCodeAccessIsNullSafe:
    """CU-868k0buex: a code-less transport error (Connection reset / timeout)
    must not raise "KeyError: key not found: code". Every place that inspects an
    error's HTTP status must do so via map.get(..., "code")."""

    def _non_throttle_retry_cases(self) -> dict:
        doc = _load()
        steps = _steps_to_map(doc["non_throttle_retry"]["steps"])
        switch = steps["check_code"]["switch"]
        return {c["condition"]: c.get("return") for c in switch}

    def test_non_throttle_retry_never_uses_bare_e_dot_code(self):
        cases = self._non_throttle_retry_cases()
        for cond in cases:
            assert "e.code" not in cond, (
                f"bare ${{e.code}} access can crash on a code-less error: {cond}"
            )
            # Every status comparison must go through map.get(e, "code").
            if "code" in cond:
                assert 'map.get(e, "code")' in cond, cond

    def test_non_throttle_retry_retries_codeless_transient_error(self):
        cases = self._non_throttle_retry_cases()
        # The branch that matches a missing/null code must retry (return True),
        # treating Connection resets / timeouts as transient.
        null_branch = [v for cond, v in cases.items() if "null" in cond]
        assert null_branch == [True], (
            f"expected code==null -> retry (True), got {cases}"
        )

    def test_non_throttle_retry_still_blocks_403_and_retries_5xx(self):
        # The CU-868jxen7m guarantees must survive the null-safe refactor.
        cases = self._non_throttle_retry_cases()
        assert [v for cond, v in cases.items() if "403" in cond] == [False]
        assert [v for cond, v in cases.items() if "500" in cond] == [True]

    def test_poll_status_throttle_check_is_null_safe(self):
        doc = _load()
        pipeline_steps = _steps_to_map(doc["report_pipeline"]["steps"])
        poll_except = _steps_to_map(pipeline_steps["poll_status"]["except"]["steps"])
        cond = poll_except["check_poll_throttle"]["switch"][0]["condition"]
        assert "poll_error.code" not in cond, cond
        assert 'map.get(poll_error, "code")' in cond, cond

    def test_throttle_retry_call_check_is_null_safe(self):
        doc = _load()
        throttle_steps = _steps_to_map(doc["throttle_retry_call"]["steps"])
        call_except = _steps_to_map(throttle_steps["attempt_call"]["except"]["steps"])
        cond = call_except["check_throttle"]["switch"][0]["condition"]
        assert "call_err.code" not in cond, cond
        assert 'map.get(call_err, "code")' in cond, cond


class TestThrottleBudgetRidesOutRateExceeded:
    """CU-868k9yg8r: the download-upload / fetch_api invocations get platform
    429 "Rate exceeded." when the service saturates during the nightly fan-out.
    Their throttle-retry budget must be large enough (>= 10) to outlast the
    saturation window so the affected pulls retry automatically and complete,
    and a 429 must be classified as a throttle (retryable via backoff)."""

    _MIN_DOWNLOAD_BUDGET = 10

    def _pipeline_steps(self) -> dict:
        return _steps_to_map(_load()["report_pipeline"]["steps"])

    def test_download_upload_uses_throttle_retry_call(self):
        step = self._pipeline_steps()["download_upload"]
        assert step["call"] == "throttle_retry_call"

    def test_download_upload_budget_is_large_enough(self):
        args = self._pipeline_steps()["download_upload"]["args"]
        assert args["max_throttle_retries"] >= self._MIN_DOWNLOAD_BUDGET, (
            f"download-upload throttle budget too small to survive a 'Rate "
            f"exceeded.' burst: {args['max_throttle_retries']}"
        )

    def test_api_call_fetch_budget_is_large_enough(self):
        args = self._pipeline_steps()["api_call_fetch"]["args"]
        assert args["max_throttle_retries"] >= self._MIN_DOWNLOAD_BUDGET, (
            f"fetch_api throttle budget too small to survive a 'Rate exceeded.' "
            f"burst: {args['max_throttle_retries']}"
        )

    def test_429_is_treated_as_a_throttle_and_backs_off(self):
        doc = _load()
        throttle_steps = _steps_to_map(doc["throttle_retry_call"]["steps"])
        call_except = _steps_to_map(throttle_steps["attempt_call"]["except"]["steps"])
        first_branch = call_except["check_throttle"]["switch"][0]
        # A 429 within the retry budget must route to the backoff step, not raise.
        assert "429" in first_branch["condition"]
        assert first_branch["next"] == "throttle_backoff"


class TestSupabaseSecretFetch:
    """§3: the three Firestore PATCH call sites become PostgREST calls
    authenticated by a Supabase service-role key fetched via the Secret
    Manager connector, instead of the workflow SA's ambient OAuth2 identity."""

    def test_get_supabase_key_subworkflow_exists(self):
        doc = _load()
        assert "get_supabase_key" in doc
        assert doc["get_supabase_key"]["params"] == []

    def test_get_supabase_key_calls_secretmanager_connector(self):
        doc = _load()
        steps = _steps_to_map(doc["get_supabase_key"]["steps"])
        fetch = steps["fetch_secret"]
        assert fetch["call"] == "googleapis.secretmanager.v1.projects.secrets.versions.access"
        name_expr = fetch["args"]["name"]
        assert "__SUPABASE_SECRET_NAME__" in name_expr
        assert 'sys.get_env("GOOGLE_CLOUD_PROJECT_ID")' in name_expr
        assert fetch["result"] == "secret_response"

    def test_get_supabase_key_decodes_the_payload(self):
        doc = _load()
        steps = _steps_to_map(doc["get_supabase_key"]["steps"])
        decode_step = [s for s in steps.values() if "return" in s][0]
        ret = decode_step["return"]
        assert "base64.decode" in ret
        assert "text.decode" in ret
        assert "secret_response.payload.data" in ret

    def test_patch_job_failed_fetches_key_before_patching(self):
        doc = _load()
        main_steps = _steps_to_map(doc["main"]["steps"])
        except_steps = _steps_to_map(main_steps["execute_pipeline"]["except"]["steps"])
        mark_failed = except_steps["mark_job_failed"]
        patch_branch = next(c for c in mark_failed["switch"] if "steps" in c)
        branch_steps = _steps_to_map(patch_branch["steps"])

        assert "fetch_supabase_key" in branch_steps
        assert branch_steps["fetch_supabase_key"]["call"] == "get_supabase_key"
        assert branch_steps["fetch_supabase_key"]["result"] == "supabase_key"

    def test_patch_job_failed_is_postgrest_not_firestore(self):
        doc = _load()
        main_steps = _steps_to_map(doc["main"]["steps"])
        except_steps = _steps_to_map(main_steps["execute_pipeline"]["except"]["steps"])
        mark_failed = except_steps["mark_job_failed"]
        patch_branch = next(c for c in mark_failed["switch"] if "steps" in c)
        branch_steps = _steps_to_map(patch_branch["steps"])
        args = branch_steps["patch_job_failed"]["args"]

        assert "firestore.googleapis.com" not in args["url"]
        assert "SUPABASE_URL" in args["url"]
        assert "/rest/v1/jobs?id=eq." in args["url"]
        assert args["url"].endswith("args.job_id}'") or "args.job_id" in args["url"]
        assert "auth" not in args
        assert args["headers"]["apikey"] == "${supabase_key}"
        assert "Bearer" in args["headers"]["Authorization"]
        assert args["headers"]["Prefer"] == "return=minimal"

        body = args["body"]
        assert body["status"] == "failed"
        assert "fields" not in body
        assert "user_message" in body["error_details"]["message"]
        assert body["error_details"]["phase"] == "workflow_error"

    def test_mark_ingest_failed_is_postgrest_not_firestore(self):
        doc = _load()
        pipeline_steps = _steps_to_map(doc["report_pipeline"]["steps"])
        ingest_except = _steps_to_map(pipeline_steps["ingest_bigquery"]["except"]["steps"])

        assert "fetch_supabase_key" in ingest_except
        assert ingest_except["fetch_supabase_key"]["call"] == "get_supabase_key"

        args = ingest_except["mark_ingest_failed"]["args"]
        assert "firestore.googleapis.com" not in args["url"]
        assert "SUPABASE_URL" in args["url"]
        assert "/rest/v1/jobs?id=eq." in args["url"]
        assert "create_response.body.job_id" in args["url"]
        assert "auth" not in args
        assert args["headers"]["apikey"] == "${supabase_key}"
        assert args["headers"]["Prefer"] == "return=minimal"

        body = args["body"]
        assert body["ingest_status"] == "failed"
        assert "fields" not in body
        assert "ingest_error" in body["ingest_error"]
