"""Tests for shared Firestore helpers — focused on client resolution."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

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


# ---------------------------------------------------------------------------
# _schedule_is_due — the "still due?" predicate used by the atomic claim
# ---------------------------------------------------------------------------

class TestScheduleIsDue:
    def _now(self) -> datetime:
        return datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)

    def test_due_when_next_run_in_past(self):
        now = self._now()
        assert firestore_utils._schedule_is_due(
            {"next_run_at": now - timedelta(minutes=5)}, now
        )

    def test_not_due_when_next_run_in_future(self):
        now = self._now()
        assert not firestore_utils._schedule_is_due(
            {"next_run_at": now + timedelta(days=7)}, now
        )

    def test_due_at_exact_boundary(self):
        now = self._now()
        assert firestore_utils._schedule_is_due({"next_run_at": now}, now)

    def test_missing_next_run_is_due(self):
        now = self._now()
        assert firestore_utils._schedule_is_due({}, now)

    def test_none_data_is_not_due(self):
        now = self._now()
        assert not firestore_utils._schedule_is_due(None, now)

    def test_naive_timestamp_coerced_to_utc(self):
        """A naive next_run_at must not raise; it is treated as UTC."""
        now = self._now()
        naive_past = (now - timedelta(hours=1)).replace(tzinfo=None)
        assert firestore_utils._schedule_is_due({"next_run_at": naive_past}, now)


# ---------------------------------------------------------------------------
# claim_due_schedule — atomic, exactly-once claim of a due schedule
# ---------------------------------------------------------------------------

class TestClaimDueSchedule:
    def _setup(self, snapshot_data, exists=True):
        now = datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)
        next_run = now + timedelta(days=7)

        snapshot = MagicMock()
        snapshot.exists = exists
        snapshot.to_dict.return_value = snapshot_data
        ref = MagicMock()
        ref.get.return_value = snapshot
        txn = MagicMock()
        db = MagicMock()
        db.collection.return_value.document.return_value = ref
        db.transaction.return_value = txn
        return now, next_run, db, ref, txn

    def test_claims_when_due(self):
        now, next_run, db, ref, txn = self._setup(
            {"next_run_at": datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc)}
        )
        with (
            patch("shared.firestore_utils.get_db", return_value=db),
            patch("shared.firestore_utils.firestore.transactional", lambda f: f),
        ):
            result = firestore_utils.claim_due_schedule("s1", now, next_run)

        assert result is True
        txn.update.assert_called_once()
        # The winner advances both run-time fields.
        _, updates = txn.update.call_args[0]
        assert updates["next_run_at"] == next_run
        assert updates["last_run_at"] == now

    def test_skips_when_already_claimed(self):
        """A concurrent run already advanced next_run_at into the future."""
        now, next_run, db, ref, txn = self._setup(
            {"next_run_at": datetime(2026, 7, 8, 10, 0, tzinfo=timezone.utc)}
        )
        with (
            patch("shared.firestore_utils.get_db", return_value=db),
            patch("shared.firestore_utils.firestore.transactional", lambda f: f),
        ):
            result = firestore_utils.claim_due_schedule("s1", now, next_run)

        assert result is False
        txn.update.assert_not_called()

    def test_missing_schedule_not_claimed(self):
        now, next_run, db, ref, txn = self._setup(None, exists=False)
        with (
            patch("shared.firestore_utils.get_db", return_value=db),
            patch("shared.firestore_utils.firestore.transactional", lambda f: f),
        ):
            result = firestore_utils.claim_due_schedule("s1", now, next_run)

        assert result is False
        txn.update.assert_not_called()

    def test_transaction_error_treated_as_not_claimed(self):
        """Contention/transient errors must never double-fan-out."""
        now, next_run, db, ref, txn = self._setup({"next_run_at": None})
        ref.get.side_effect = RuntimeError("aborted")
        with (
            patch("shared.firestore_utils.get_db", return_value=db),
            patch("shared.firestore_utils.firestore.transactional", lambda f: f),
        ):
            result = firestore_utils.claim_due_schedule("s1", now, next_run)

        assert result is False


# ---------------------------------------------------------------------------
# try_claim_job_launch — launch-level idempotency guard
# ---------------------------------------------------------------------------

class TestTryClaimJobLaunch:
    def _db_with_ref(self):
        ref = MagicMock()
        db = MagicMock()
        db.collection.return_value.document.return_value = ref
        return db, ref

    def test_new_key_is_claimed(self):
        db, ref = self._db_with_ref()
        with patch("shared.firestore_utils.get_db", return_value=db):
            assert firestore_utils.try_claim_job_launch("k1") is True
        ref.create.assert_called_once()

    def test_duplicate_key_returns_false(self):
        from google.api_core.exceptions import AlreadyExists

        db, ref = self._db_with_ref()
        ref.create.side_effect = AlreadyExists("exists")
        with patch("shared.firestore_utils.get_db", return_value=db):
            assert firestore_utils.try_claim_job_launch("k1") is False

    def test_fails_open_on_firestore_error(self):
        """A Firestore hiccup must not silently drop a report — proceed."""
        db, ref = self._db_with_ref()
        ref.create.side_effect = RuntimeError("boom")
        with patch("shared.firestore_utils.get_db", return_value=db):
            assert firestore_utils.try_claim_job_launch("k1") is True

    def test_distinct_keys_map_to_distinct_docs(self):
        db, ref = self._db_with_ref()
        with patch("shared.firestore_utils.get_db", return_value=db):
            firestore_utils.try_claim_job_launch("k1")
            firestore_utils.try_claim_job_launch("k2")
        doc_ids = {c[0][0] for c in db.collection.return_value.document.call_args_list}
        assert len(doc_ids) == 2
