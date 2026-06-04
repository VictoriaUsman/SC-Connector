"""Tests for shared Firestore helpers — focused on client resolution."""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))

from shared import firestore_utils  # noqa: E402


@pytest.fixture(autouse=True)
def _mock_firestore():
    with patch("shared.firestore_utils.firestore.Client"):
        yield


# A representative client roster mirroring the production data referenced by the
# ticket: an exact-id client (Acme), one whose id differs only by casing
# (Matini), one matched by display name (Ummi), and an unrelated client that a
# loose matcher could mistakenly return (Jack N' Jill).
ROSTER = [
    {"id": "acme", "name": "Acme"},
    {"id": "matini", "name": "Matini"},
    {"id": "ummi", "name": "Ummi"},
    {"id": "jacknjill", "name": "Jack N' Jill"},
]


def _patch_roster(by_id: dict[str, dict] | None = None):
    """Patch get_client (exact doc-id lookup) and list_clients (full scan)."""
    by_id = by_id if by_id is not None else {c["id"]: c for c in ROSTER}

    def fake_get_client(client_id):
        return by_id.get(client_id)

    return (
        patch("shared.firestore_utils.get_client", side_effect=fake_get_client),
        patch("shared.firestore_utils.list_clients", return_value=ROSTER),
    )


class TestResolveClient:
    def test_exact_id_match(self):
        p1, p2 = _patch_roster()
        with p1, p2:
            resolved = firestore_utils.resolve_client("acme")
        assert resolved is not None
        assert resolved["id"] == "acme"

    def test_case_insensitive_id_match_resolves_matini(self):
        """Matini: identifier differs from the stored id only by casing.

        Previously this returned no exact doc and surfaced 'Client not found'.
        """
        p1, p2 = _patch_roster()
        with p1, p2:
            resolved = firestore_utils.resolve_client("Matini")
        assert resolved is not None
        assert resolved["id"] == "matini"

    def test_name_match_resolves_to_correct_client_not_neighbour(self):
        """Ummi: must resolve to Ummi, never to the unrelated Jack N' Jill."""
        # Simulate an identifier ("Ummi") that has no exact doc id.
        by_id = {c["id"]: c for c in ROSTER}
        del by_id["ummi"]  # no doc keyed exactly "Ummi"
        p1, p2 = _patch_roster(by_id)
        with p1, p2:
            resolved = firestore_utils.resolve_client("Ummi")
        assert resolved is not None
        assert resolved["id"] == "ummi"
        assert resolved["name"] == "Ummi"

    def test_spot_check_other_client_resolves(self):
        p1, p2 = _patch_roster()
        with p1, p2:
            resolved = firestore_utils.resolve_client("jacknjill")
        assert resolved is not None
        assert resolved["id"] == "jacknjill"

    def test_missing_client_returns_none(self):
        p1, p2 = _patch_roster()
        with p1, p2:
            assert firestore_utils.resolve_client("does-not-exist") is None

    def test_empty_identifier_returns_none(self):
        p1, p2 = _patch_roster()
        with p1, p2:
            assert firestore_utils.resolve_client("") is None

    def test_ambiguous_name_match_returns_none(self):
        """Never guess when more than one client matches — avoids mismapping."""
        roster = [
            {"id": "ummi-1", "name": "Ummi"},
            {"id": "ummi-2", "name": "Ummi"},
        ]
        with (
            patch("shared.firestore_utils.get_client", return_value=None),
            patch("shared.firestore_utils.list_clients", return_value=roster),
        ):
            assert firestore_utils.resolve_client("Ummi") is None

    def test_exact_id_preferred_over_name_collision(self):
        """An exact doc-id hit wins even if another client's name collides."""
        roster = [
            {"id": "ummi", "name": "Ummi"},
            {"id": "jacknjill", "name": "ummi"},  # name collides with the id above
        ]
        with (
            patch("shared.firestore_utils.get_client", return_value={"id": "ummi", "name": "Ummi"}),
            patch("shared.firestore_utils.list_clients", return_value=roster),
        ):
            resolved = firestore_utils.resolve_client("ummi")
        assert resolved["id"] == "ummi"
