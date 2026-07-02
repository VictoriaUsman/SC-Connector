"""Tests for the duplicate-Drive-file scanner (scripts/scan_duplicate_drive_files.py)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import scan_duplicate_drive_files as scanner  # noqa: E402

_FOLDER = "application/vnd.google-apps.folder"


class TestFindDuplicateFiles:
    def test_no_duplicates(self):
        files = [
            {"name": "a.tsv", "id": "1"},
            {"name": "b.tsv", "id": "2"},
        ]
        assert scanner.find_duplicate_files(files) == {}

    def test_flags_duplicate_name(self):
        files = [
            {"name": "spCampaigns_2026-06-16_to_2026-06-29_Rolio_US.tsv", "id": "1"},
            {"name": "spCampaigns_2026-06-16_to_2026-06-29_Rolio_US.tsv", "id": "2"},
            {"name": "other.tsv", "id": "3"},
        ]
        dupes = scanner.find_duplicate_files(files)
        assert list(dupes) == ["spCampaigns_2026-06-16_to_2026-06-29_Rolio_US.tsv"]
        assert len(dupes["spCampaigns_2026-06-16_to_2026-06-29_Rolio_US.tsv"]) == 2

    def test_three_copies(self):
        files = [{"name": "sbCampaigns.tsv", "id": str(i)} for i in range(3)]
        dupes = scanner.find_duplicate_files(files)
        assert len(dupes["sbCampaigns.tsv"]) == 3

    def test_ignores_subfolders(self):
        files = [
            {"name": "2026-06-16", "id": "d1", "mimeType": _FOLDER},
            {"name": "2026-06-16", "id": "d2", "mimeType": _FOLDER},
            {"name": "report.tsv", "id": "f1"},
        ]
        # Two folders with the same name are handled by the folder-dedup logic,
        # not this file scanner — so nothing is flagged here.
        assert scanner.find_duplicate_files(files) == {}


class _FakeFilesApi:
    def __init__(self, children_by_parent):
        self._children_by_parent = children_by_parent
        self._last_parent = None

    def list(self, q, **kwargs):
        # Parse the parent id out of "'<id>' in parents and ..."
        parent = q.split("'")[1]
        self._last_parent = parent
        return self

    def execute(self):
        parent = self._last_parent
        return {"files": list(self._children_by_parent.get(parent, []))}


class _FakeService:
    def __init__(self, children_by_parent):
        self._api = _FakeFilesApi(children_by_parent)

    def files(self):
        return self._api


class TestScanFolder:
    def test_recurses_and_collects_duplicates(self):
        tree = {
            "root": [
                {"name": "US", "id": "us", "mimeType": _FOLDER},
            ],
            "us": [
                {"name": "spCampaigns", "id": "sp", "mimeType": _FOLDER},
                {"name": "sbCampaigns", "id": "sb", "mimeType": _FOLDER},
            ],
            "sp": [
                {"name": "spCampaigns_R_US.tsv", "id": "1", "size": "40000"},
                {"name": "spCampaigns_R_US.tsv", "id": "2", "size": "40000"},
            ],
            "sb": [
                {"name": "sbCampaigns_G_US.tsv", "id": "3", "size": "1000"},
                {"name": "sbCampaigns_G_US.tsv", "id": "4", "size": "4000"},
                {"name": "sbCampaigns_G_US.tsv", "id": "5", "size": "4000"},
            ],
        }
        service = _FakeService(tree)
        findings: list = []
        scanner.scan_folder(service, "root", "(root)", None, findings)

        paths = {f["path"] for f in findings}
        assert paths == {"(root)/US/spCampaigns", "(root)/US/sbCampaigns"}
        counts = {f["name"]: len(f["files"]) for f in findings}
        assert counts["spCampaigns_R_US.tsv"] == 2
        assert counts["sbCampaigns_G_US.tsv"] == 3

    def test_contains_filter(self):
        tree = {
            "root": [
                {"name": "keep_2026-06-16_to_2026-06-29.tsv", "id": "1"},
                {"name": "keep_2026-06-16_to_2026-06-29.tsv", "id": "2"},
                {"name": "other_2026-01-01.tsv", "id": "3"},
                {"name": "other_2026-01-01.tsv", "id": "4"},
            ],
        }
        service = _FakeService(tree)
        findings: list = []
        scanner.scan_folder(service, "root", "(root)", "2026-06-16_to_2026-06-29", findings)

        assert len(findings) == 1
        assert findings[0]["name"] == "keep_2026-06-16_to_2026-06-29.tsv"

    def test_clean_tree_no_findings(self):
        tree = {"root": [{"name": "a.tsv", "id": "1"}, {"name": "b.tsv", "id": "2"}]}
        findings: list = []
        scanner.scan_folder(_FakeService(tree), "root", "(root)", None, findings)
        assert findings == []
