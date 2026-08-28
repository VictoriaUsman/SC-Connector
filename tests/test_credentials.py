"""Tests for shared.credentials — Amazon credential assembly.

Focus: marketplace-aware Ads profile resolution. Amazon Ads profiles are scoped
to a single marketplace/country, so a multi-marketplace account (one seller
account / region) needs a distinct profile per marketplace. Resolving a single
``ads_profile_id`` for every marketplace meant non-primary marketplaces hit the
wrong profile and returned empty ads (the "ads data not pulling" symptom).
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))

os.environ.setdefault("GCP_PROJECT", "test-project")
os.environ.setdefault("ENVIRONMENT", "staging")

import shared.credentials as credentials  # noqa: E402

_APP_CREDS = {"client_id": "app-client", "client_secret": "app-secret", "refresh_token": "app-rt"}


@pytest.fixture()
def _app_creds_secret():
    # Only the app-credentials secret is read in the ads_profile_id path.
    with patch.object(credentials, "_read_secret", return_value=_APP_CREDS):
        yield


class TestResolveAdsProfileId:
    def test_per_marketplace_map_wins(self):
        client = {"ads_profile_id": "default", "ads_profile_ids": {"US": "111", "CA": "222"}}
        assert credentials._resolve_ads_profile_id(client, "CA") == "222"

    def test_falls_back_to_default_when_marketplace_absent_from_map(self):
        client = {"ads_profile_id": "default", "ads_profile_ids": {"US": "111"}}
        assert credentials._resolve_ads_profile_id(client, "CA") == "default"

    def test_falls_back_to_default_when_no_marketplace_supplied(self):
        client = {"ads_profile_id": "default", "ads_profile_ids": {"US": "111"}}
        assert credentials._resolve_ads_profile_id(client, None) == "default"

    def test_legacy_single_profile_only(self):
        client = {"ads_profile_id": "default"}
        assert credentials._resolve_ads_profile_id(client, "US") == "default"


class TestGetAdsCredentialsMarketplaceAware:
    def test_uses_per_marketplace_profile(self, _app_creds_secret):
        client = {"ads_profile_id": "111", "ads_profile_ids": {"US": "111", "CA": "222"}}
        with patch.object(credentials, "get_client", return_value=client):
            creds = credentials.get_ads_credentials("c1", "CA")
        assert creds["profile_id"] == "222"
        assert creds["refresh_token"] == "app-rt"
        assert creds["client_id"] == "app-client"

    def test_falls_back_to_default_profile_for_unmapped_marketplace(self, _app_creds_secret):
        client = {"ads_profile_id": "111", "ads_profile_ids": {"US": "111"}}
        with patch.object(credentials, "get_client", return_value=client):
            creds = credentials.get_ads_credentials("c1", "DE")
        assert creds["profile_id"] == "111"

    def test_legacy_clients_unchanged_without_marketplace(self, _app_creds_secret):
        client = {"ads_profile_id": "111"}
        with patch.object(credentials, "get_client", return_value=client):
            creds = credentials.get_ads_credentials("c1")
        assert creds["profile_id"] == "111"

    def test_per_marketplace_map_overrides_legacy_secret_profile(self):
        client = {"ads_api_secret_name": "kalilos-staging-ads-c1", "ads_profile_ids": {"DE": "999"}}

        def fake_read_secret(name: str) -> dict:
            if name.endswith("ads-c1"):
                return {"refresh_token": "client-rt", "profile_id": "secret-profile"}
            return _APP_CREDS

        with (
            patch.object(credentials, "get_client", return_value=client),
            patch.object(credentials, "_read_secret", side_effect=fake_read_secret),
        ):
            creds = credentials.get_ads_credentials("c1", "DE")
        # The per-marketplace map wins over the profile baked into the secret.
        assert creds["profile_id"] == "999"
        assert creds["refresh_token"] == "client-rt"


class TestReadSecret:
    def test_local_mode_reads_from_local_secrets(self, monkeypatch):
        monkeypatch.setenv("LOCAL_MODE", "true")
        try:
            with patch("shared.credentials.resolve_secret", return_value='{"client_id": "abc"}') as mock_resolve:
                result = credentials._read_secret("kalilos-staging-sp-api-app-credentials")
            assert result == {"client_id": "abc"}
            mock_resolve.assert_called_once_with("kalilos-staging-sp-api-app-credentials")
        finally:
            monkeypatch.delenv("LOCAL_MODE", raising=False)

    def test_production_mode_uses_secret_manager(self, monkeypatch):
        monkeypatch.delenv("LOCAL_MODE", raising=False)
        fake_resp = MagicMock()
        fake_resp.payload.data = b'{"client_id": "real"}'
        with patch.object(credentials, "_get_sm") as mock_sm:
            mock_sm.return_value.access_secret_version.return_value = fake_resp
            result = credentials._read_secret("kalilos-staging-sp-api-app-credentials")
        assert result == {"client_id": "real"}
