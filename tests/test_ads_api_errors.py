"""Tests for Ads API unauthorized 3P profile error detection and handling."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest
from ad_api.base.exceptions import AdvertisingApiException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "functions"))

from shared.ads_api_errors import (  # noqa: E402
    AdsProfileUnauthorizedError,
    ad_type_for_report,
    extract_profile_id,
    is_ads_profile_unauthorized,
    raise_if_ads_profile_unauthorized,
    unauthorized_message,
)


def _load_handler(function_name: str) -> ModuleType:
    """Load a Cloud Function ``main`` module under a unique name.

    Each function package has a top-level ``main`` module, so a plain
    ``import main`` would collide. We load each with a distinct module name.
    """
    path = (
        Path(__file__).resolve().parent.parent
        / "functions"
        / function_name
        / "main.py"
    )
    module_name = f"_handler_{function_name}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module

# Real-world Amazon Ads error wording from the failing brook-whittle / boss-audio jobs.
_UNAUTHORIZED_DETAIL = (
    "Unauthorized exception while handling 3P Request: Customer: AVCDL6XIHIB1E "
    "does not have access to profile: 913929500"
)


def _unauthorized_exc(code: int = 401) -> AdvertisingApiException:
    return AdvertisingApiException(
        code,
        {"code": str(code), "details": _UNAUTHORIZED_DETAIL},
        {},
    )


class TestAdTypeForReport:
    def test_sponsored_products(self):
        assert ad_type_for_report("spCampaigns") == "SP (Sponsored Products)"

    def test_sponsored_brands(self):
        assert ad_type_for_report("sbCampaigns") == "SB (Sponsored Brands)"

    def test_sponsored_display(self):
        assert ad_type_for_report("sdCampaigns") == "SD (Sponsored Display)"

    def test_unknown_report_returns_none(self):
        assert ad_type_for_report("not-a-report") is None

    def test_none_report(self):
        assert ad_type_for_report(None) is None


class TestIsAdsProfileUnauthorized:
    def test_detects_3p_unauthorized_message(self):
        assert is_ads_profile_unauthorized(_unauthorized_exc()) is True

    def test_detects_plain_exception_message(self):
        assert is_ads_profile_unauthorized(Exception(_UNAUTHORIZED_DETAIL)) is True

    def test_detects_no_access_to_profile_wording(self):
        assert is_ads_profile_unauthorized(
            Exception("Customer does not have access to profile: 12345")
        ) is True

    def test_ignores_unrelated_errors(self):
        assert is_ads_profile_unauthorized(ValueError("something else")) is False

    def test_ignores_duplicate_425(self):
        dup = AdvertisingApiException(
            425, {"code": "425", "detail": "duplicate of : abc"}, {}
        )
        assert is_ads_profile_unauthorized(dup) is False

    def test_detects_401_with_unauthorized_wording(self):
        exc = AdvertisingApiException(401, {"code": "401", "details": "Unauthorized"}, {})
        assert is_ads_profile_unauthorized(exc) is True


class TestExtractProfileId:
    def test_extracts_profile_from_message(self):
        assert extract_profile_id(_unauthorized_exc()) == "913929500"

    def test_returns_none_when_absent(self):
        assert extract_profile_id(Exception("no profile here")) is None


class TestUnauthorizedMessage:
    def test_includes_client_profile_and_ad_type(self):
        msg = unauthorized_message(
            client_id="brook-whittle",
            profile_id="913929500",
            ad_type="SD (Sponsored Display)",
        )
        assert "brook-whittle" in msg
        assert "913929500" in msg
        assert "SD (Sponsored Display)" in msg
        assert "retrying will not help" in msg.lower()
        assert "re-grant access" in msg.lower()


class TestRaiseIfAdsProfileUnauthorized:
    def test_reraises_unrelated_errors(self):
        with pytest.raises(RuntimeError, match="boom"):
            raise_if_ads_profile_unauthorized(
                RuntimeError("boom"),
                client_id="brook-whittle",
                marketplace="US",
                report_type="spCampaigns",
            )

    def test_wraps_unauthorized_with_context(self):
        with pytest.raises(AdsProfileUnauthorizedError) as exc_info:
            raise_if_ads_profile_unauthorized(
                _unauthorized_exc(),
                client_id="brook-whittle",
                marketplace="US",
                report_type="sdCampaigns",
            )

        err = exc_info.value
        assert err.client_id == "brook-whittle"
        assert err.marketplace == "US"
        assert err.report_type == "sdCampaigns"
        assert err.profile_id == "913929500"
        assert err.ad_type == "SD (Sponsored Display)"

    def test_explicit_profile_id_takes_precedence(self):
        with pytest.raises(AdsProfileUnauthorizedError) as exc_info:
            raise_if_ads_profile_unauthorized(
                Exception("Unauthorized exception while handling 3P Request"),
                client_id="boss-audio",
                marketplace="US",
                report_type="sbCampaigns",
                profile_id="319903939",
            )
        assert exc_info.value.profile_id == "319903939"

    def test_log_context_includes_diagnostics(self):
        with pytest.raises(AdsProfileUnauthorizedError) as exc_info:
            raise_if_ads_profile_unauthorized(
                _unauthorized_exc(),
                client_id="brook-whittle",
                marketplace="US",
                report_type="spCampaigns",
            )
        ctx = exc_info.value.log_context()
        assert ctx["client_id"] == "brook-whittle"
        assert ctx["profile_id"] == "913929500"
        assert ctx["ad_type"] == "SP (Sponsored Products)"
        assert ctx["report_type"] == "spCampaigns"


class TestAdsApiClientWrapsUnauthorized:
    def _client_module(self):
        shared_dir = Path(__file__).resolve().parent.parent / "functions"
        if str(shared_dir) not in sys.path:
            sys.path.insert(0, str(shared_dir))
        from shared import ads_api_client  # noqa: WPS433

        return ads_api_client

    def test_create_report_wraps_unauthorized(self):
        ads_api_client = self._client_module()
        fake = MagicMock()
        fake.post_report.side_effect = _unauthorized_exc()

        with patch.object(ads_api_client, "_client", return_value=fake):
            with pytest.raises(AdsProfileUnauthorizedError) as exc_info:
                ads_api_client.create_report(
                    credentials={"profile_id": "913929500"},
                    marketplace="US",
                    report_config={"name": "x"},
                    client_id="brook-whittle",
                    report_type="spCampaigns",
                )
        assert exc_info.value.profile_id == "913929500"

    def test_get_report_wraps_unauthorized(self):
        ads_api_client = self._client_module()
        fake = MagicMock()
        fake.get_report.side_effect = _unauthorized_exc()

        with patch.object(ads_api_client, "_client", return_value=fake):
            with pytest.raises(AdsProfileUnauthorizedError):
                ads_api_client.get_report(
                    {"profile_id": "913929500"},
                    "US",
                    "report-1",
                    client_id="boss-audio",
                    report_type="sdCampaigns",
                )

    def test_create_report_still_reuses_duplicate_425(self):
        ads_api_client = self._client_module()
        fake = MagicMock()
        fake.post_report.side_effect = AdvertisingApiException(
            425,
            {"code": "425", "detail": "duplicate of : 123e4567-e89b-12d3-a456-426614174000"},
            {},
        )

        with patch.object(ads_api_client, "_client", return_value=fake):
            report_id = ads_api_client.create_report(
                credentials={"profile_id": "1"},
                marketplace="US",
                report_config={"name": "x"},
                client_id="brook-whittle",
                report_type="spCampaigns",
            )
        assert report_id == "123e4567-e89b-12d3-a456-426614174000"


class TestCreateReportUnauthorizedHandler:
    def test_returns_401_and_does_not_retry(self):
        create_report_main = _load_handler("create_report")

        request = MagicMock()
        request.get_json.return_value = {
            "api_source": "ads_api",
            "client_id": "brook-whittle",
            "marketplace": "US",
            "report_type": "sdCampaigns",
            "job_id": "job-ads-1",
            "report_params": {"startDate": "2026-06-01", "endDate": "2026-06-01"},
        }

        with (
            patch.object(create_report_main, "ads_api_client") as mock_client_module,
            patch.object(create_report_main, "get_ads_credentials", return_value={"profile_id": "913929500"}),
            patch.object(create_report_main, "update_job_status") as mock_update,
        ):
            mock_client_module.create_report.side_effect = AdsProfileUnauthorizedError(
                unauthorized_message(
                    client_id="brook-whittle",
                    profile_id="913929500",
                    ad_type="SD (Sponsored Display)",
                ),
                client_id="brook-whittle",
                marketplace="US",
                report_type="sdCampaigns",
                profile_id="913929500",
                ad_type="SD (Sponsored Display)",
            )

            body, status = create_report_main.handler(request)

        assert status == 401
        assert body["code"] == "UNAUTHORIZED"
        assert "brook-whittle" in body["error"]
        failed_call = mock_update.call_args_list[-1]
        assert failed_call.args[0] == "job-ads-1"
        assert failed_call.args[1] == "failed"
        assert failed_call.kwargs["error_details"]["code"] == "UNAUTHORIZED"
        assert failed_call.kwargs["error_details"]["phase"] == "create_report"


class TestPollStatusUnauthorizedHandler:
    def test_returns_401_and_marks_failed(self):
        poll_status_main = _load_handler("poll_status")

        request = MagicMock()
        request.get_json.return_value = {
            "api_source": "ads_api",
            "client_id": "boss-audio",
            "marketplace": "US",
            "report_id": "report-9",
            "report_type": "sbCampaigns",
            "job_id": "job-ads-2",
        }

        with (
            patch.object(poll_status_main, "ads_api_client") as mock_client_module,
            patch.object(poll_status_main, "get_ads_credentials", return_value={"profile_id": "319903939"}),
            patch.object(poll_status_main, "update_job_status") as mock_update,
            patch.object(poll_status_main, "update_job"),
        ):
            mock_client_module.get_report.side_effect = AdsProfileUnauthorizedError(
                unauthorized_message(
                    client_id="boss-audio",
                    profile_id="319903939",
                    ad_type="SB (Sponsored Brands)",
                ),
                client_id="boss-audio",
                marketplace="US",
                report_type="sbCampaigns",
                profile_id="319903939",
                ad_type="SB (Sponsored Brands)",
            )

            body, status = poll_status_main.handler(request)

        assert status == 401
        assert body["code"] == "UNAUTHORIZED"
        failed_call = mock_update.call_args_list[-1]
        assert failed_call.args[0] == "job-ads-2"
        assert failed_call.args[1] == "failed"
        assert failed_call.kwargs["error_details"]["code"] == "UNAUTHORIZED"
        assert failed_call.kwargs["error_details"]["phase"] == "poll_status"
