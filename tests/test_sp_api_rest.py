"""Tests for shared.sp_api_rest — the generic SP-API REST transport."""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))

os.environ.setdefault("GCP_PROJECT", "test-project")
os.environ.setdefault("ENVIRONMENT", "staging")


def _resp(status: int, json_body=None, text: str = "") -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.content = b"x" if (json_body is not None or text) else b""
    r.text = text
    r.json.return_value = json_body if json_body is not None else {}
    if json_body is None and not text:
        r.json.side_effect = ValueError("no json")
    return r


@pytest.fixture(autouse=True)
def _fast_sleep():
    with patch("shared.sp_api_rest.time.sleep"):
        yield


@pytest.fixture(autouse=True)
def _token():
    with patch("shared.sp_api_rest._access_token", return_value="tok-123"):
        yield


class TestSpApiRequest:
    def test_get_returns_json_and_sets_token_header(self):
        from shared.sp_api_rest import sp_api_request

        with patch("shared.sp_api_rest.requests.request", return_value=_resp(200, {"ok": True})) as req:
            out = sp_api_request("moxe", "US", "GET", "/replenishment/2022-11-07/offers")

        assert out == {"ok": True}
        _, kwargs = req.call_args
        assert kwargs["headers"]["x-amz-access-token"] == "tok-123"
        # NA marketplace resolves to the NA host.
        assert req.call_args[0][1].startswith("https://sellingpartnerapi-na.amazon.com")

    def test_retries_on_429_then_succeeds(self):
        from shared.sp_api_rest import sp_api_request

        seq = [_resp(429, text="slow down"), _resp(200, {"done": 1})]
        with patch("shared.sp_api_rest.requests.request", side_effect=seq) as req:
            out = sp_api_request("moxe", "US", "POST", "/x", body={"a": 1})

        assert out == {"done": 1}
        assert req.call_count == 2

    def test_raises_on_client_error(self):
        from shared.sp_api_rest import SPAPIRequestError, sp_api_request

        with patch("shared.sp_api_rest.requests.request", return_value=_resp(403, text="denied")):
            with pytest.raises(SPAPIRequestError) as exc:
                sp_api_request("moxe", "US", "GET", "/x")

        assert exc.value.status_code == 403

    def test_429_message_is_throttle_detectable(self):
        from shared.sp_api_rest import sp_api_request
        from shared.throttle import is_throttled

        with patch("shared.sp_api_rest.requests.request", return_value=_resp(429, text="rate")):
            with pytest.raises(Exception) as exc:
                sp_api_request("moxe", "US", "GET", "/x")

        assert is_throttled(exc.value)


class TestPaginate:
    def test_follows_next_token_then_stops(self):
        from shared.sp_api_rest import paginate

        page1 = {"offers": [1], "pagination": {"nextToken": "t2"}}
        page2 = {"offers": [2]}  # no token -> stop
        with patch("shared.sp_api_rest.sp_api_request", side_effect=[page1, page2]) as req:
            pages = list(paginate("moxe", "US", "POST", "/x", body={"marketplaceId": "M"}))

        assert pages == [page1, page2]
        assert req.call_count == 2
        # Second call carries the nextToken injected into the body.
        second_kwargs = req.call_args_list[1].kwargs
        assert second_kwargs["body"]["nextToken"] == "t2"

    def test_respects_max_pages(self):
        from shared.sp_api_rest import paginate

        endless = {"offers": [1], "pagination": {"nextToken": "more"}}
        with patch("shared.sp_api_rest.sp_api_request", return_value=endless):
            pages = list(paginate("moxe", "US", "POST", "/x", max_pages=3))

        assert len(pages) == 3
