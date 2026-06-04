"""Tests for the cached + retrying LWA token exchange."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "functions"))

from shared import lwa_token  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_cache():
    lwa_token._cache.clear()
    yield
    lwa_token._cache.clear()


def _response(status: int, body: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = body or {}
    return resp


def _token(**kwargs) -> str:
    return lwa_token.get_access_token(
        refresh_token="refresh", client_id="app", client_secret="secret", **kwargs
    )


def test_caches_token_across_calls():
    ok = _response(200, {"access_token": "tok", "expires_in": 3600})
    with patch.object(lwa_token.requests, "post", return_value=ok) as post:
        assert _token() == "tok"
        assert _token() == "tok"
    assert post.call_count == 1


def test_force_refresh_bypasses_cache():
    ok = _response(200, {"access_token": "tok", "expires_in": 3600})
    with patch.object(lwa_token.requests, "post", return_value=ok) as post:
        _token()
        _token(force_refresh=True)
    assert post.call_count == 2


def test_short_lived_token_is_refreshed():
    # expires_in below the safety margin -> treated as already expired.
    ok = _response(200, {"access_token": "tok", "expires_in": 10})
    with patch.object(lwa_token.requests, "post", return_value=ok) as post:
        _token()
        _token()
    assert post.call_count == 2


def test_retries_transient_then_succeeds():
    responses = [_response(503), _response(200, {"access_token": "tok", "expires_in": 3600})]
    with (
        patch.object(lwa_token.requests, "post", side_effect=responses) as post,
        patch.object(lwa_token.time, "sleep"),
    ):
        assert _token() == "tok"
    assert post.call_count == 2


def test_permanent_error_fails_fast_without_retry():
    bad = _response(400)
    bad.raise_for_status.side_effect = requests.HTTPError("invalid_grant")
    with (
        patch.object(lwa_token.requests, "post", return_value=bad) as post,
        patch.object(lwa_token.time, "sleep"),
    ):
        with pytest.raises(requests.HTTPError):
            _token()
    assert post.call_count == 1
