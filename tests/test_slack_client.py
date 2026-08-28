"""Tests for shared Slack client helpers — channel resolution and formatting."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))

from shared.slack_client import resolve_target_channels


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
