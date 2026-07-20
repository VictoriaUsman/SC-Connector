"""Tests for Drive client — Firestore-coordinated folder creation, hierarchy, and upload logic."""

from __future__ import annotations

import os
import sys
from datetime import date
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))


@pytest.fixture(autouse=True)
def _reset_service():
    """Reset cached singletons between tests."""
    import shared.drive_client as dc
    dc._service = None
    dc._db = None
    yield
    dc._service = None
    dc._db = None


@pytest.fixture()
def mock_service():
    with patch("shared.drive_client.get_service") as m:
        svc = MagicMock()
        m.return_value = svc
        yield svc


def _mock_lock_ref(*, create_raises=False, poll_folder_id=None):
    """Build a mock Firestore lock document reference."""
    from google.api_core.exceptions import AlreadyExists

    lock_ref = MagicMock()
    if create_raises:
        lock_ref.create.side_effect = AlreadyExists("already exists")
    else:
        lock_ref.create.return_value = None

    if poll_folder_id:
        doc_snap = MagicMock()
        doc_snap.exists = True
        doc_snap.to_dict.return_value = {"folder_id": poll_folder_id, "status": "created"}
        lock_ref.get.return_value = doc_snap
    else:
        doc_snap = MagicMock()
        doc_snap.exists = True
        doc_snap.to_dict.return_value = {"status": "creating"}
        lock_ref.get.return_value = doc_snap

    return lock_ref


@pytest.fixture()
def mock_db():
    with patch("shared.drive_client._get_db") as m:
        db = MagicMock()
        m.return_value = db
        yield db


# ---------------------------------------------------------------------------
# _list_matching_folders
# ---------------------------------------------------------------------------

class TestListMatchingFolders:
    def test_returns_sorted_results(self, mock_service):
        from shared.drive_client import _list_matching_folders

        mock_service.files().list().execute.return_value = {
            "files": [
                {"id": "older", "createdTime": "2026-03-01T00:00:00Z"},
                {"id": "newer", "createdTime": "2026-03-02T00:00:00Z"},
            ]
        }
        result = _list_matching_folders("2026-03-20", "root-id")
        assert result == [
            {"id": "older", "createdTime": "2026-03-01T00:00:00Z"},
            {"id": "newer", "createdTime": "2026-03-02T00:00:00Z"},
        ]

    def test_empty_when_no_matches(self, mock_service):
        from shared.drive_client import _list_matching_folders

        mock_service.files().list().execute.return_value = {"files": []}
        result = _list_matching_folders("missing", "root-id")
        assert result == []


# ---------------------------------------------------------------------------
# find_or_create_folder — Firestore-coordinated creation
# ---------------------------------------------------------------------------

class TestFindOrCreateFolder:
    def test_returns_existing_folder(self, mock_service, mock_db):
        """When folder already exists in Drive, return it without creating."""
        from shared.drive_client import find_or_create_folder

        mock_service.files().list().execute.return_value = {
            "files": [{"id": "existing-1", "createdTime": "2026-03-01T00:00:00Z"}]
        }

        result = find_or_create_folder("2026-03-20", "root-id")
        assert result == "existing-1"
        mock_service.files().create.assert_not_called()
        mock_db.collection.assert_not_called()

    def test_wins_lock_and_creates(self, mock_service, mock_db):
        """First caller wins the Firestore lock, creates the Drive folder."""
        from shared.drive_client import find_or_create_folder

        lock_ref = _mock_lock_ref(create_raises=False)
        mock_db.collection().document.return_value = lock_ref

        list_responses = [
            {"files": []},  # initial find_folder
            {"files": []},  # double-check after winning lock
        ]
        mock_service.files().list().execute.side_effect = list_responses
        mock_service.files().create().execute.return_value = {"id": "new-1"}

        result = find_or_create_folder("2026-03-20", "root-id")
        assert result == "new-1"
        lock_ref.set.assert_called_once()
        assert lock_ref.set.call_args[0][0]["folder_id"] == "new-1"

    def test_loses_lock_and_waits(self, mock_service, mock_db):
        """Second caller loses the lock, polls Firestore for the folder ID."""
        from google.api_core.exceptions import AlreadyExists
        from shared.drive_client import find_or_create_folder

        lock_ref = MagicMock()
        lock_ref.create.side_effect = AlreadyExists("already exists")

        creating_snap = MagicMock(exists=True)
        creating_snap.to_dict.return_value = {"status": "creating"}

        created_snap = MagicMock(exists=True)
        created_snap.to_dict.return_value = {"folder_id": "winner-folder", "status": "created"}

        lock_ref.get.side_effect = [creating_snap, created_snap]
        mock_db.collection().document.return_value = lock_ref

        mock_service.files().list().execute.return_value = {"files": []}

        result = find_or_create_folder("2026-03-20", "root-id")
        assert result == "winner-folder"
        mock_service.files().create.assert_not_called()

    def test_lock_winner_finds_folder_appeared(self, mock_service, mock_db):
        """Winner's double-check finds the folder (Drive propagated), skips create."""
        from shared.drive_client import find_or_create_folder

        lock_ref = _mock_lock_ref(create_raises=False)
        mock_db.collection().document.return_value = lock_ref

        list_responses = [
            {"files": []},  # initial find_folder
            {"files": [{"id": "appeared", "createdTime": "2026-03-20T00:00:00Z"}]},  # double-check
        ]
        mock_service.files().list().execute.side_effect = list_responses

        result = find_or_create_folder("2026-03-20", "root-id")
        assert result == "appeared"
        mock_service.files().create.assert_not_called()
        lock_ref.delete.assert_called_once()

    @patch("shared.drive_client.time.sleep")
    def test_lock_timeout_falls_back_to_drive(self, mock_sleep, mock_service, mock_db):
        """If the lock holder never writes folder_id, fall back to Drive search."""
        from shared.drive_client import find_or_create_folder

        lock_ref = _mock_lock_ref(create_raises=True, poll_folder_id=None)
        mock_db.collection().document.return_value = lock_ref

        list_responses = [
            {"files": []},  # initial find_folder
            {"files": [{"id": "late-folder", "createdTime": "2026-03-20T00:00:00Z"}]},  # timeout fallback
        ]
        mock_service.files().list().execute.side_effect = list_responses

        import shared.drive_client as dc
        original_timeout = dc._LOCK_TIMEOUT_SECS
        dc._LOCK_TIMEOUT_SECS = 0
        try:
            result = find_or_create_folder("2026-03-20", "root-id")
        finally:
            dc._LOCK_TIMEOUT_SECS = original_timeout

        assert result == "late-folder"

    @patch("shared.drive_client.time.sleep")
    def test_stale_lock_uses_dedup_not_retry(self, mock_sleep, mock_service, mock_db):
        """When lock references a deleted folder, fall to dedup path (not recursive retry)."""
        from google.api_core.exceptions import AlreadyExists
        from shared.drive_client import find_or_create_folder

        lock_ref = MagicMock()
        lock_ref.create.side_effect = AlreadyExists("already exists")

        stale_snap = MagicMock(exists=True)
        stale_snap.to_dict.return_value = {"folder_id": "deleted-folder", "status": "created"}
        lock_ref.get.return_value = stale_snap

        mock_db.collection().document.return_value = lock_ref

        list_responses = [
            {"files": []},  # initial find_folder
            {"files": []},  # stale check find_folder (folder gone)
            {"files": []},  # dedup re-check
            {"files": [{"id": "new-folder", "createdTime": "2026-03-21T00:00:00Z"}]},  # dedup post-create
        ]
        mock_service.files().list().execute.side_effect = list_responses
        mock_service.files().create().execute.return_value = {"id": "new-folder"}

        result = find_or_create_folder("2026-03-21", "root-id")
        assert result == "new-folder"
        lock_ref.delete.assert_called_once()

    @patch("shared.drive_client.time.sleep")
    def test_firestore_down_uses_dedup_fallback(self, mock_sleep, mock_service, mock_db):
        """If Firestore is unavailable, fall back through _create_folder_with_dedup."""
        from shared.drive_client import find_or_create_folder

        mock_db.collection.side_effect = RuntimeError("Firestore unavailable")

        list_responses = [
            {"files": []},  # initial find_folder
            {"files": []},  # re-check inside _create_folder_with_dedup
            {"files": [{"id": "fallback-1", "createdTime": "2026-03-20T00:00:00Z"}]},  # post-create dedup check
        ]
        mock_service.files().list().execute.side_effect = list_responses
        mock_service.files().create().execute.return_value = {"id": "fallback-1"}

        result = find_or_create_folder("2026-03-20", "root-id")
        assert result == "fallback-1"
        mock_sleep.assert_called()


# ---------------------------------------------------------------------------
# _create_folder_with_dedup — dedup safety net
# ---------------------------------------------------------------------------

class TestCreateFolderWithDedup:
    @patch("shared.drive_client.time.sleep")
    def test_recheck_finds_folder_skips_create(self, mock_sleep, mock_service):
        """If the folder appears during the delay, no creation happens."""
        from shared.drive_client import _create_folder_with_dedup

        mock_service.files().list().execute.return_value = {
            "files": [{"id": "appeared", "createdTime": "2026-03-20T00:00:00Z"}]
        }
        result = _create_folder_with_dedup("2026-03-20", "root-id")
        assert result == "appeared"
        mock_service.files().create.assert_not_called()

    @patch("shared.drive_client.time.sleep")
    def test_creates_and_dedup_keeps_oldest(self, mock_sleep, mock_service):
        """When duplicates exist after creation, keep the oldest and delete the rest."""
        from shared.drive_client import _create_folder_with_dedup

        list_responses = [
            {"files": []},  # re-check (nothing yet)
            {"files": [  # post-create dedup finds two
                {"id": "older-folder", "createdTime": "2026-03-20T00:00:00Z"},
                {"id": "my-folder", "createdTime": "2026-03-20T00:00:01Z"},
            ]},
        ]
        mock_service.files().list().execute.side_effect = list_responses
        mock_service.files().create().execute.return_value = {"id": "my-folder"}

        result = _create_folder_with_dedup("2026-03-20", "root-id")
        assert result == "older-folder"
        mock_service.files().delete.assert_called_once()

    @patch("shared.drive_client.time.sleep")
    def test_creates_single_no_dedup_needed(self, mock_sleep, mock_service):
        """When no duplicates exist, just return the created folder."""
        from shared.drive_client import _create_folder_with_dedup

        list_responses = [
            {"files": []},  # re-check
            {"files": [{"id": "only-one", "createdTime": "2026-03-20T00:00:00Z"}]},  # dedup check
        ]
        mock_service.files().list().execute.side_effect = list_responses
        mock_service.files().create().execute.return_value = {"id": "only-one"}

        result = _create_folder_with_dedup("2026-03-20", "root-id")
        assert result == "only-one"
        mock_service.files().delete.assert_not_called()


# ---------------------------------------------------------------------------
# Concurrent folder creation simulation
# ---------------------------------------------------------------------------

class TestConcurrentFolderCreation:
    """Simulate two concurrent callers hitting find_or_create_folder.

    This is the exact scenario that causes duplicate Drive folders:
    two workflow executions (US and CA) for the same schedule arrive
    at download_upload around the same time and both try to create
    the date folder.
    """

    @patch("shared.drive_client._assert_no_duplicates")
    @patch("shared.drive_client.time.sleep")
    def test_concurrent_callers_with_lock(self, mock_sleep, mock_dedup, mock_service, mock_db):
        """Two threads call find_or_create_folder — Firestore lock ensures
        the winner creates the folder and the loser gets the winner's ID."""
        import threading
        from google.api_core.exceptions import AlreadyExists
        from shared.drive_client import find_or_create_folder

        lock_claimed = threading.Event()
        winner_folder_id = "the-one-folder"

        def mock_lock_create(data):
            if lock_claimed.is_set():
                raise AlreadyExists("lock exists")
            lock_claimed.set()

        lock_ref = MagicMock()
        lock_ref.create.side_effect = mock_lock_create

        won_snap = MagicMock(exists=True)
        won_snap.to_dict.return_value = {"folder_id": winner_folder_id, "status": "created"}
        lock_ref.get.return_value = won_snap

        mock_db.collection().document.return_value = lock_ref

        mock_service.files().list().execute.return_value = {"files": []}
        mock_service.files().create().execute.return_value = {"id": winner_folder_id}

        results: list[str] = []
        errors: list[Exception] = []

        def caller():
            try:
                results.append(find_or_create_folder("2026-03-21", "root-id"))
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=caller)
        t2 = threading.Thread(target=caller)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert not errors, f"Unexpected errors: {errors}"
        assert len(results) == 2
        assert results[0] == results[1] == winner_folder_id, (
            f"Both callers must get the same folder ID '{winner_folder_id}', "
            f"got {results}"
        )


# ---------------------------------------------------------------------------
# build_folder_path — hierarchy layouts
# ---------------------------------------------------------------------------

class TestBuildFolderPath:
    @pytest.fixture(autouse=True)
    def _skip_dedup_guard(self):
        """Dedup guard is tested separately — don't let it consume mock responses."""
        with patch("shared.drive_client._assert_no_duplicates"):
            yield

    def _setup_find_or_create(self, mock_service, folder_map: dict[str, str]):
        """Mock find_or_create_folder to map folder names to IDs."""
        call_count = {"n": 0}
        folder_ids = list(folder_map.values())

        list_responses = []
        for fid in folder_ids:
            list_responses.append({"files": [{"id": fid, "createdTime": "2026-01-01T00:00:00Z"}]})

        mock_service.files().list().execute.side_effect = list_responses

    def test_default_layout(self, mock_service):
        """Default: {root}/{date}/{client}/{marketplace}/{report_type}"""
        from shared.drive_client import build_folder_path

        self._setup_find_or_create(mock_service, {
            "2026-03-20": "date-id",
            "testy": "client-id",
            "US": "market-id",
            "GET_SALES_AND_TRAFFIC_REPORT": "report-id",
        })

        folder_id, path = build_folder_path(
            root_folder_id="root-id",
            client_name="testy",
            marketplace="US",
            api_source="sp_api",
            report_type="GET_SALES_AND_TRAFFIC_REPORT",
            report_date=date(2026, 3, 20),
        )
        assert folder_id == "report-id"
        assert path == "2026-03-20/testy/US/GET_SALES_AND_TRAFFIC_REPORT"

    def test_custom_folder_with_date_subfolder(self, mock_service):
        """Custom: {root}/{folder_name}/{date}/{client}/{marketplace}/{report_type}"""
        from shared.drive_client import build_folder_path

        self._setup_find_or_create(mock_service, {
            "testem": "custom-id",
            "2026-03-20": "date-id",
            "testy": "client-id",
            "US": "market-id",
            "GET_SALES_AND_TRAFFIC_REPORT": "report-id",
        })

        folder_id, path = build_folder_path(
            root_folder_id="root-id",
            client_name="testy",
            marketplace="US",
            api_source="sp_api",
            report_type="GET_SALES_AND_TRAFFIC_REPORT",
            report_date=date(2026, 3, 20),
            folder_name="testem",
            subfolder_strategy="date",
        )
        assert folder_id == "report-id"
        assert path == "testem/2026-03-20/testy/US/GET_SALES_AND_TRAFFIC_REPORT"

    def test_custom_folder_flat(self, mock_service):
        """Custom flat: {root}/{folder_name}/{client}/{marketplace}/{report_type}"""
        from shared.drive_client import build_folder_path

        self._setup_find_or_create(mock_service, {
            "testem": "custom-id",
            "testy": "client-id",
            "US": "market-id",
            "GET_SALES_AND_TRAFFIC_REPORT": "report-id",
        })

        folder_id, path = build_folder_path(
            root_folder_id="root-id",
            client_name="testy",
            marketplace="US",
            api_source="sp_api",
            report_type="GET_SALES_AND_TRAFFIC_REPORT",
            report_date=date(2026, 3, 20),
            folder_name="testem",
            subfolder_strategy="none",
        )
        assert folder_id == "report-id"
        assert path == "testem/testy/US/GET_SALES_AND_TRAFFIC_REPORT"

    def test_folder_name_whitespace_is_stripped(self, mock_service):
        """A folder_name with surrounding whitespace must resolve to the same
        path as the trimmed name, so "MTD Ads KPIs " and "MTD Ads KPIs" don't
        create two visually-identical Drive folders."""
        from shared.drive_client import build_folder_path

        self._setup_find_or_create(mock_service, {
            "MTD Ads KPIs": "custom-id",
            "testy": "client-id",
            "US": "market-id",
            "GET_SALES_AND_TRAFFIC_REPORT": "report-id",
        })

        folder_id, path = build_folder_path(
            root_folder_id="root-id",
            client_name="testy",
            marketplace="US",
            api_source="sp_api",
            report_type="GET_SALES_AND_TRAFFIC_REPORT",
            report_date=date(2026, 3, 20),
            folder_name="  MTD Ads KPIs  ",
            subfolder_strategy="none",
        )
        assert folder_id == "report-id"
        assert path == "MTD Ads KPIs/testy/US/GET_SALES_AND_TRAFFIC_REPORT"


# ---------------------------------------------------------------------------
# upload_or_replace
# ---------------------------------------------------------------------------

class TestUploadOrReplace:
    @pytest.fixture(autouse=True)
    def _mock_file_index(self):
        """Patch the Firestore file-index so upload_or_replace never touches a
        real Firestore client. By default the index has no prior file recorded
        (``exists`` is False), so the deterministic dedupe is a no-op unless a
        test opts in via ``_set_recorded_file``."""
        with patch("shared.drive_client._get_db") as m:
            db = MagicMock()
            snap = MagicMock()
            snap.exists = False
            snap.to_dict.return_value = {}
            db.collection().document().get.return_value = snap
            m.return_value = db
            self._index_db = db
            self._index_snap = snap
            yield db

    def _set_recorded_file(self, file_id: str) -> None:
        """Make the mocked Firestore index report a previously-uploaded file."""
        self._index_snap.exists = True
        self._index_snap.to_dict.return_value = {"file_id": file_id}

    def test_uploads_new_file(self, mock_service):
        from shared.drive_client import upload_or_replace

        mock_service.files().list().execute.return_value = {"files": []}
        mock_service.files().create().execute.return_value = {"id": "file-1"}

        result = upload_or_replace("report.json", b'{"data": 1}', "folder-1")
        assert result == "file-1"
        mock_service.files().delete.assert_not_called()

    def test_replaces_existing_file(self, mock_service):
        from shared.drive_client import upload_or_replace

        mock_service.files().list().execute.return_value = {
            "files": [{"id": "old-file"}]
        }
        mock_service.files().create().execute.return_value = {"id": "new-file"}

        result = upload_or_replace("report.json", b'{"data": 2}', "folder-1")
        assert result == "new-file"
        mock_service.files().delete.assert_called_once()

    def test_converts_small_tsv_to_google_sheet(self, mock_service):
        from shared.drive_client import upload_or_replace

        mock_service.files().list().execute.return_value = {"files": []}
        mock_service.files().create().execute.return_value = {"id": "sheet-1"}

        upload_or_replace(
            "report.tsv", b"col1\tcol2\nval1\tval2", "folder-1",
            mime_type="text/tab-separated-values",
        )

        create_call = mock_service.files().create.call_args
        body = create_call.kwargs.get("body") or create_call[1].get("body")
        assert body["mimeType"] == "application/vnd.google-apps.spreadsheet"

    def test_converted_sheet_stored_without_extension(self, mock_service):
        """A TSV converted to a Sheet must be stored under its extension-less
        stem, matching the title Drive assigns — so future replace searches
        (which look for the stem) always find and replace it."""
        from shared.drive_client import upload_or_replace

        mock_service.files().list().execute.return_value = {"files": []}
        mock_service.files().create().execute.return_value = {"id": "sheet-1"}

        upload_or_replace(
            "sbCampaigns_2026-06-23_to_2026-07-06_Foamma_US.tsv",
            b"col1\tcol2\nval1\tval2", "folder-1",
            mime_type="text/tab-separated-values",
        )

        create_call = mock_service.files().create.call_args
        body = create_call.kwargs.get("body") or create_call[1].get("body")
        assert body["name"] == "sbCampaigns_2026-06-23_to_2026-07-06_Foamma_US"

    def test_replace_search_matches_extension_stripped_sheet(self, mock_service):
        """Regression for recurring duplicate Sheets (CU-868k7evq5).

        Drive drops the extension when converting a TSV to a Sheet, so a report
        uploaded as ``<name>.tsv`` is stored as ``<name>``. The replace search
        must look for BOTH names, otherwise the already-converted Sheet is never
        found, never deleted, and each re-upload stacks another duplicate."""
        from shared.drive_client import upload_or_replace

        mock_service.files().list().execute.return_value = {"files": []}
        mock_service.files().create().execute.return_value = {"id": "new-sheet"}

        upload_or_replace(
            "report.tsv", b"col1\tcol2\nval1\tval2", "folder-1",
            mime_type="text/tab-separated-values",
        )

        search_queries = [
            c.kwargs.get("q")
            for c in mock_service.files().list.call_args_list
            if c.kwargs.get("q")
        ]
        assert search_queries, "expected a search query for existing files"
        q = search_queries[-1]
        assert "name='report.tsv'" in q
        assert "name='report'" in q

    def test_replaces_already_converted_duplicate_sheet(self, mock_service):
        """End-to-end: an existing extension-less Sheet is deleted before the
        new upload, so the folder converges to exactly one file per report."""
        from shared.drive_client import upload_or_replace

        # The broadened search surfaces the previously-converted Sheet (whose
        # stored title has no extension).
        mock_service.files().list().execute.return_value = {
            "files": [{"id": "stale-sheet"}]
        }
        mock_service.files().create().execute.return_value = {"id": "fresh-sheet"}

        result = upload_or_replace(
            "spCampaigns_2026-06-23_to_2026-07-06_Rolio_US.tsv",
            b"col1\tcol2\nval1\tval2", "folder-1",
            mime_type="text/tab-separated-values",
        )

        assert result == "fresh-sheet"
        mock_service.files().delete.assert_called_once()
        deleted_id = (
            mock_service.files().delete.call_args.kwargs.get("fileId")
            or mock_service.files().delete.call_args[1].get("fileId")
        )
        assert deleted_id == "stale-sheet"

    def test_deletes_recorded_prior_file_by_id_despite_search_lag(self, mock_service):
        """Regression for recurring duplicate Sheets (CU-868k7evq5).

        The workflow retries download-upload when its http call times out (the
        SB campaigns pull runs for minutes), re-running the whole upload. Drive's
        name search is eventually consistent, so the retry does not yet see the
        Sheet the prior attempt created and the name-based delete finds nothing —
        every retry stacked another duplicate. The Firestore-recorded file id is
        strongly consistent, so the retry must delete the prior file by id even
        when the name search returns empty."""
        from shared.drive_client import upload_or_replace

        # Name search returns nothing (search index has not caught up yet)...
        mock_service.files().list().execute.return_value = {"files": []}
        # ...but a prior attempt recorded its file id in Firestore.
        self._set_recorded_file("prior-retry-file")
        mock_service.files().create().execute.return_value = {"id": "new-file"}

        result = upload_or_replace(
            "sbCampaigns_2026-07-05_to_2026-07-18_GloveStation_US.tsv",
            b"col1\tcol2\nval1\tval2", "folder-1",
            mime_type="text/tab-separated-values",
        )

        assert result == "new-file"
        deleted_ids = [
            (c.kwargs.get("fileId") or (c[1].get("fileId") if len(c) > 1 else None))
            for c in mock_service.files().delete.call_args_list
        ]
        assert "prior-retry-file" in deleted_ids

    def test_records_uploaded_file_id_in_index(self, mock_service):
        """After uploading, the new file id is written to the Firestore index
        under the stored (extension-less for Sheets) name so the next upload can
        delete it deterministically."""
        from shared.drive_client import upload_or_replace

        mock_service.files().list().execute.return_value = {"files": []}
        mock_service.files().create().execute.return_value = {"id": "recorded-1"}

        upload_or_replace(
            "spCampaigns_2026-07-05_to_2026-07-18_Rolio_US.tsv",
            b"col1\tcol2\nval1\tval2", "folder-1",
            mime_type="text/tab-separated-values",
        )

        set_calls = self._index_db.collection().document().set.call_args_list
        assert set_calls, "expected the uploaded file id to be recorded"
        recorded = set_calls[-1][0][0]
        assert recorded["file_id"] == "recorded-1"
        # Sheets-convertible reports are stored (and tracked) without extension.
        assert recorded["name"] == "spCampaigns_2026-07-05_to_2026-07-18_Rolio_US"

    def test_recorded_delete_is_best_effort(self, mock_service):
        """A Firestore error while deleting the recorded file must never fail the
        upload — the guard fails open."""
        from shared.drive_client import upload_or_replace

        self._index_db.collection().document().get.side_effect = RuntimeError("firestore down")
        mock_service.files().list().execute.return_value = {"files": []}
        mock_service.files().create().execute.return_value = {"id": "resilient-1"}

        result = upload_or_replace(
            "report.tsv", b"col1\tcol2\nval1\tval2", "folder-1",
            mime_type="text/tab-separated-values",
        )
        assert result == "resilient-1"

    def test_json_replace_search_uses_exact_name_only(self, mock_service):
        """Non-convertible types (JSON/XML) keep their extension in Drive, so
        the stem is not added to the search (avoids matching unrelated files)."""
        from shared.drive_client import upload_or_replace

        mock_service.files().list().execute.return_value = {"files": []}
        mock_service.files().create().execute.return_value = {"id": "file-json"}

        upload_or_replace(
            "report.json", b'{"data": 1}', "folder-1",
            mime_type="application/json",
        )

        search_queries = [
            c.kwargs.get("q")
            for c in mock_service.files().list.call_args_list
            if c.kwargs.get("q")
        ]
        assert search_queries
        q = search_queries[-1]
        assert "name='report.json'" in q
        assert "name='report'" not in q

    def test_converts_small_csv_to_google_sheet(self, mock_service):
        from shared.drive_client import upload_or_replace

        mock_service.files().list().execute.return_value = {"files": []}
        mock_service.files().create().execute.return_value = {"id": "sheet-2"}

        upload_or_replace(
            "report.csv", b"col1,col2\nval1,val2", "folder-1",
            mime_type="text/csv",
        )

        create_call = mock_service.files().create.call_args
        body = create_call.kwargs.get("body") or create_call[1].get("body")
        assert body["mimeType"] == "application/vnd.google-apps.spreadsheet"

    def test_skips_sheets_conversion_for_large_tsv(self, mock_service):
        from shared.drive_client import upload_or_replace, _SHEETS_SIZE_LIMIT

        mock_service.files().list().execute.return_value = {"files": []}
        mock_service.files().create().execute.return_value = {"id": "file-big"}

        large_content = b"x" * (_SHEETS_SIZE_LIMIT + 1)
        upload_or_replace(
            "report.tsv", large_content, "folder-1",
            mime_type="text/tab-separated-values",
        )

        create_call = mock_service.files().create.call_args
        body = create_call.kwargs.get("body") or create_call[1].get("body")
        assert "mimeType" not in body

    def test_skips_sheets_conversion_for_json(self, mock_service):
        from shared.drive_client import upload_or_replace

        mock_service.files().list().execute.return_value = {"files": []}
        mock_service.files().create().execute.return_value = {"id": "file-json"}

        upload_or_replace(
            "report.json", b'{"data": 1}', "folder-1",
            mime_type="application/json",
        )

        create_call = mock_service.files().create.call_args
        body = create_call.kwargs.get("body") or create_call[1].get("body")
        assert "mimeType" not in body


# ---------------------------------------------------------------------------
# infer_report_format
# ---------------------------------------------------------------------------

class TestInferReportFormat:
    @pytest.mark.parametrize("report_type,expected_ext,expected_mime", [
        ("GET_FLAT_FILE_OPEN_LISTINGS_DATA", ".tsv", "text/tab-separated-values"),
        ("GET_XML_ALL_ORDERS_DATA_BY_ORDER_DATE_GENERAL", ".xml", "application/xml"),
        ("GET_CSV_MFN_PRIME_RETURNS_REPORT", ".csv", "text/csv"),
        ("GET_MERCHANT_LISTINGS_ALL_DATA", ".tsv", "text/tab-separated-values"),
    ])
    def test_sp_api_name_heuristic(self, report_type, expected_ext, expected_mime):
        from shared.drive_client import infer_report_format

        ext, mime = infer_report_format("sp_api", report_type)
        assert ext == expected_ext
        assert mime == expected_mime

    @pytest.mark.parametrize("report_type", [
        "GET_SALES_AND_TRAFFIC_REPORT",
        "GET_BRAND_ANALYTICS_SEARCH_TERMS_REPORT",
        "GET_BRAND_ANALYTICS_MARKET_BASKET_REPORT",
        "GET_LEDGER_SUMMARY_VIEW_DATA",
    ])
    def test_sp_api_json_reports(self, report_type):
        from shared.drive_client import infer_report_format

        ext, mime = infer_report_format("sp_api", report_type)
        assert ext == ".json"
        assert mime == "application/json"

    def test_ads_api_always_tsv(self):
        from shared.drive_client import infer_report_format

        ext, mime = infer_report_format("ads_api", "spCampaigns")
        assert ext == ".tsv"
        assert mime == "text/tab-separated-values"
