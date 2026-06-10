"""Tests for shared.ads_api_rest — the generic Ads API REST transport."""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))

os.environ.setdefault("GCP_PROJECT", "test-project")
os.environ.setdefault("ENVIRONMENT", "staging")

_CREDS = {
    "refresh_token": "rt",
    "client_id": "amzn-client",
    "client_secret": "secret",
    "profile_id": "999",
}


def _resp(status: int, json_body=None, text: str = "") -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.content = b"x" if (json_body is not None or text) else b""
    r.text = text
    r.json.return_value = json_body if json_body is not None else {}
    return r


@pytest.fixture(autouse=True)
def _fast_sleep():
    with patch("shared.ads_api_rest.time.sleep"):
        yield


@pytest.fixture(autouse=True)
def _creds_and_token():
    with patch("shared.ads_api_rest.get_ads_credentials", return_value=_CREDS), \
         patch("shared.ads_api_rest.get_access_token", return_value="bearer-1"):
        yield


class TestAdsApiRequest:
    def test_sets_auth_clientid_and_scope_headers(self):
        from shared.ads_api_rest import ads_api_request

        with patch("shared.ads_api_rest.requests.request", return_value=_resp(200, {"ok": 1})) as req:
            out = ads_api_request("moxe", "US", "GET", "/v2/profiles")

        assert out == {"ok": 1}
        headers = req.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer bearer-1"
        assert headers["Amazon-Advertising-API-ClientId"] == "amzn-client"
        assert headers["Amazon-Advertising-API-Scope"] == "999"

    def test_retries_on_500_then_succeeds(self):
        from shared.ads_api_rest import ads_api_request

        seq = [_resp(503, text="busy"), _resp(200, {"done": 1})]
        with patch("shared.ads_api_rest.requests.request", side_effect=seq) as req:
            out = ads_api_request("moxe", "US", "POST", "/x", body={"a": 1})

        assert out == {"done": 1}
        assert req.call_count == 2

    def test_raises_on_client_error(self):
        from shared.ads_api_rest import AdsAPIRequestError, ads_api_request

        with patch("shared.ads_api_rest.requests.request", return_value=_resp(400, text="bad")):
            with pytest.raises(AdsAPIRequestError) as exc:
                ads_api_request("moxe", "US", "GET", "/x")

        assert exc.value.status_code == 400
