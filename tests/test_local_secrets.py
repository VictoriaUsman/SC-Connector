"""Tests for shared.local_secrets — LOCAL_MODE secret resolution."""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))

import pytest

from shared import local_secrets


@pytest.fixture(autouse=True)
def _reset_cache():
    local_secrets._local_secrets_cache = None
    yield
    local_secrets._local_secrets_cache = None


class TestIsLocalMode:
    def test_true_when_env_var_true(self, monkeypatch):
        monkeypatch.setenv("LOCAL_MODE", "true")
        assert local_secrets.is_local_mode() is True

    def test_false_when_unset(self, monkeypatch):
        monkeypatch.delenv("LOCAL_MODE", raising=False)
        assert local_secrets.is_local_mode() is False

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("LOCAL_MODE", "TRUE")
        assert local_secrets.is_local_mode() is True

    def test_false_for_other_values(self, monkeypatch):
        monkeypatch.setenv("LOCAL_MODE", "false")
        assert local_secrets.is_local_mode() is False


class TestResolveSecret:
    def test_reads_value_from_file(self, tmp_path, monkeypatch):
        secrets_file = tmp_path / "local-secrets.json"
        secrets_file.write_text(json.dumps({"my-secret": "xoxb-abc123"}))
        monkeypatch.setattr(local_secrets, "_LOCAL_SECRETS_FILE", secrets_file)

        assert local_secrets.resolve_secret("my-secret") == "xoxb-abc123"

    def test_missing_key_raises_with_helpful_message(self, tmp_path, monkeypatch):
        secrets_file = tmp_path / "local-secrets.json"
        secrets_file.write_text(json.dumps({}))
        monkeypatch.setattr(local_secrets, "_LOCAL_SECRETS_FILE", secrets_file)

        with pytest.raises(KeyError, match="my-secret"):
            local_secrets.resolve_secret("my-secret")

    def test_missing_file_raises_with_helpful_message(self, tmp_path, monkeypatch):
        secrets_file = tmp_path / "does-not-exist.json"
        monkeypatch.setattr(local_secrets, "_LOCAL_SECRETS_FILE", secrets_file)

        with pytest.raises(FileNotFoundError, match="local-secret-set"):
            local_secrets.resolve_secret("my-secret")

    def test_caches_after_first_load(self, tmp_path, monkeypatch):
        secrets_file = tmp_path / "local-secrets.json"
        secrets_file.write_text(json.dumps({"a": "1"}))
        monkeypatch.setattr(local_secrets, "_LOCAL_SECRETS_FILE", secrets_file)

        local_secrets.resolve_secret("a")
        secrets_file.write_text(json.dumps({"a": "2"}))
        assert local_secrets.resolve_secret("a") == "1"
