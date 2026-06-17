"""Tests for SP-API forbidden error detection and handling."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sp_api.base.exceptions import SellingApiForbiddenException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "functions"))

from shared.sp_api_errors import (  # noqa: E402
    FORBIDDEN_ACTIONABLE_MESSAGE,
    SPAPIForbiddenError,
    extract_report_date,
    forbidden_message,
    is_sp_api_forbidden,
    raise_if_sp_api_forbidden,
)


class TestIsSpApiForbidden:
    def test_detects_sdk_forbidden_exception(self):
        exc = SellingApiForbiddenException(
            [{"code": "Forbidden", "message": "Access to the resource is forbidden", "details": ""}]
        )
        assert is_sp_api_forbidden(exc) is True

    def test_detects_custom_forbidden_error(self):
        exc = SPAPIForbiddenError(
            FORBIDDEN_ACTIONABLE_MESSAGE,
            client_id="foamrush",
            marketplace="US",
            report_type="GET_SALES_AND_TRAFFIC_REPORT",
        )
        assert is_sp_api_forbidden(exc) is True

    def test_ignores_unrelated_errors(self):
        assert is_sp_api_forbidden(ValueError("something else")) is False

    def test_detects_message_pattern(self):
        assert is_sp_api_forbidden(Exception("Access to the resource is forbidden")) is True

    def test_detects_denied_wording(self):
        # The dominant real-world Amazon message uses "denied", not "forbidden".
        assert is_sp_api_forbidden(
            Exception("[{'code': 'Unauthorized', 'message': 'Access to requested resource is denied.'}]")
        ) is True


class TestForbiddenMessage:
    def test_role_gated_report_includes_role_hint(self):
        msg = forbidden_message("GET_SALES_AND_TRAFFIC_REPORT")
        assert "Selling Partner Insights" in msg
        assert "permissions" in msg.lower()

    def test_generic_report_has_no_false_role_claim(self):
        msg = forbidden_message("GET_MERCHANT_LISTINGS_ALL_DATA")
        assert "Selling Partner Insights" not in msg
        assert "access denied" in msg.lower()

    def test_does_not_claim_automatic_retry(self):
        # Reviewer feedback: the 403 message must not tell operators it "is
        # retried automatically" (the pipeline does NOT auto-retry a 403). It is
        # a permissions error requiring re-authorization in Seller Central.
        for report_type in (
            "GET_SALES_AND_TRAFFIC_REPORT",
            "GET_MERCHANT_LISTINGS_ALL_DATA",
            None,
        ):
            msg = forbidden_message(report_type).lower()
            assert "retried automatically" not in msg
            assert "usually transient" not in msg
            assert "re-authorize" in msg


class TestRaiseIfSpApiForbidden:
    def test_reraises_unrelated_errors(self):
        with pytest.raises(RuntimeError, match="boom"):
            raise_if_sp_api_forbidden(
                RuntimeError("boom"),
                client_id="foamrush",
                marketplace="US",
                report_type="GET_SALES_AND_TRAFFIC_REPORT",
            )

    def test_wraps_forbidden_with_context(self):
        sdk_exc = SellingApiForbiddenException(
            [{"code": "Forbidden", "message": "Access to the resource is forbidden", "details": ""}]
        )
        with pytest.raises(SPAPIForbiddenError) as exc_info:
            raise_if_sp_api_forbidden(
                sdk_exc,
                client_id="pico",
                marketplace="US",
                report_type="GET_SALES_AND_TRAFFIC_REPORT",
                report_params={
                    "dataStartTime": "2026-05-03T07:00:00Z",
                    "dataEndTime": "2026-05-04T07:00:00Z",
                },
            )

        err = exc_info.value
        assert err.client_id == "pico"
        assert err.marketplace == "US"
        assert err.report_type == "GET_SALES_AND_TRAFFIC_REPORT"
        assert err.report_date == "2026-05-03 to 2026-05-04"
        assert "Selling Partner Insights" in str(err)


class TestExtractReportDate:
    def test_single_day_from_start(self):
        assert extract_report_date({"dataStartTime": "2026-05-03T07:00:00Z"}) == "2026-05-03"

    def test_range(self):
        assert extract_report_date({
            "dataStartTime": "2026-05-01T07:00:00Z",
            "dataEndTime": "2026-05-03T07:00:00Z",
        }) == "2026-05-01 to 2026-05-03"


class TestCreateReportForbiddenHandler:
    def test_returns_403_without_retry_payload(self):
        create_report_dir = Path(__file__).resolve().parent.parent / "functions" / "create_report"
        if str(create_report_dir) not in sys.path:
            sys.path.insert(0, str(create_report_dir))
        import main as create_report_main  # noqa: WPS433

        request = MagicMock()
        request.get_json.return_value = {
            "api_source": "sp_api",
            "client_id": "foamrush",
            "marketplace": "US",
            "report_type": "GET_SALES_AND_TRAFFIC_REPORT",
            "job_id": "job-123",
            "report_params": {"dataStartTime": "2026-05-03T07:00:00Z"},
        }

        with (
            patch.object(
                create_report_main,
                "sp_api_client",
            ) as mock_client_module,
            patch.object(create_report_main, "get_sp_credentials", return_value={"refresh_token": "x"}),
            patch.object(create_report_main, "update_job_status") as mock_update,
        ):
            mock_client_module.create_report.side_effect = SPAPIForbiddenError(
                forbidden_message("GET_SALES_AND_TRAFFIC_REPORT"),
                client_id="foamrush",
                marketplace="US",
                report_type="GET_SALES_AND_TRAFFIC_REPORT",
                report_date="2026-05-03",
            )

            body, status = create_report_main.handler(request)

        assert status == 403
        assert body["code"] == "FORBIDDEN"
        assert "Selling Partner Insights" in body["error"]
        mock_update.assert_called()
        failed_call = mock_update.call_args_list[-1]
        assert failed_call.args[0] == "job-123"
        assert failed_call.args[1] == "failed"
        assert failed_call.kwargs["error_details"]["code"] == "FORBIDDEN"
        assert failed_call.kwargs["error_details"]["phase"] == "create_report"
