"""Tests for shared Slack client helpers — channel resolution and formatting."""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))

from shared.slack_client import get_channel_tag_block, resolve_target_channels


class TestResolveTargetChannels:
    def test_multi_channel_config_returns_all_channel_ids(self):
        config = {"channels": [{"id": "C1", "name": "one"}, {"id": "C2", "name": "two"}]}
        assert resolve_target_channels(config) == ["C1", "C2"]

    def test_legacy_single_channel_field_falls_back(self):
        config = {"slack_channel_id": "C123"}
        assert resolve_target_channels(config) == ["C123"]

    def test_channels_list_takes_priority_over_legacy_field(self):
        config = {"slack_channel_id": "LEGACY", "channels": [{"id": "C1"}]}
        assert resolve_target_channels(config) == ["C1"]

    def test_test_mode_overrides_entire_channel_list(self):
        config = {
            "use_test_channel": True,
            "test_channel_id": "CTEST",
            "channels": [{"id": "C1"}, {"id": "C2"}],
        }
        assert resolve_target_channels(config) == ["CTEST"]

    def test_test_mode_without_test_channel_id_returns_empty(self):
        config = {"use_test_channel": True, "channels": [{"id": "C1"}]}
        assert resolve_target_channels(config) == []

    def test_no_channels_configured_returns_empty(self):
        assert resolve_target_channels({}) == []

    def test_channels_entries_missing_id_are_skipped(self):
        config = {"channels": [{"id": "C1"}, {"name": "no id"}, {"id": "C2"}]}
        assert resolve_target_channels(config) == ["C1", "C2"]


class TestGetChannelTagBlock:
    def test_builds_mention_block_for_single_user(self):
        config = {"channels": [{"id": "C1", "tag_user_ids": ["U111"]}]}
        block = get_channel_tag_block(config, "C1")
        assert block == {"type": "section", "text": {"type": "mrkdwn", "text": "<@U111>"}}

    def test_builds_mention_block_for_multiple_users(self):
        config = {"channels": [{"id": "C1", "tag_user_ids": ["U111", "U222"]}]}
        block = get_channel_tag_block(config, "C1")
        assert block == {"type": "section", "text": {"type": "mrkdwn", "text": "<@U111> <@U222>"}}

    def test_only_tags_the_matching_channel(self):
        config = {
            "channels": [
                {"id": "C1", "tag_user_ids": ["U111"]},
                {"id": "C2"},
            ]
        }
        assert get_channel_tag_block(config, "C1") is not None
        assert get_channel_tag_block(config, "C2") is None

    def test_returns_none_when_channel_has_no_tag_user_ids(self):
        config = {"channels": [{"id": "C1"}]}
        assert get_channel_tag_block(config, "C1") is None

    def test_returns_none_when_tag_user_ids_is_empty_list(self):
        config = {"channels": [{"id": "C1", "tag_user_ids": []}]}
        assert get_channel_tag_block(config, "C1") is None

    def test_returns_none_when_channel_id_not_found(self):
        config = {"channels": [{"id": "C1", "tag_user_ids": ["U111"]}]}
        assert get_channel_tag_block(config, "C999") is None

    def test_returns_none_for_legacy_config_without_channels_list(self):
        config = {"slack_channel_id": "C123"}
        assert get_channel_tag_block(config, "C123") is None


class TestGetSlackToken:
    def test_local_mode_uses_local_secrets(self, monkeypatch):
        import shared.slack_client as sc

        sc._slack_token = None
        monkeypatch.setenv("LOCAL_MODE", "true")
        monkeypatch.setenv("ENVIRONMENT", "staging")
        try:
            with patch("shared.slack_client.resolve_secret", return_value="xoxb-local-token") as mock_resolve:
                token = sc._get_slack_token()
            assert token == "xoxb-local-token"
            mock_resolve.assert_called_once_with("kalilos-staging-slack-bot-token")
        finally:
            sc._slack_token = None

    def test_production_mode_uses_secret_manager(self, monkeypatch):
        import shared.slack_client as sc

        sc._slack_token = None
        monkeypatch.delenv("LOCAL_MODE", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "staging")
        monkeypatch.setenv("GCP_PROJECT", "test-project")
        fake_resp = MagicMock()
        fake_resp.payload.data = b"xoxb-real-token"
        try:
            with patch("shared.slack_client._get_sm") as mock_sm:
                mock_sm.return_value.access_secret_version.return_value = fake_resp
                token = sc._get_slack_token()
            assert token == "xoxb-real-token"
        finally:
            sc._slack_token = None
