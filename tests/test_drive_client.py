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
        from shared.drive_client import find_or_create_folder

        lock_ref = _mock_lock_ref(create_raises=True, poll_folder_id="winner-folder")
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

    def test_firestore_down_falls_back(self, mock_service, mock_db):
        """If Firestore is unavailable, fall back to direct Drive create."""
        from shared.drive_client import find_or_create_folder

        mock_db.collection.side_effect = RuntimeError("Firestore unavailable")

        mock_service.files().list().execute.return_value = {"files": []}
        mock_service.files().create().execute.return_value = {"id": "fallback-1"}

        result = find_or_create_folder("2026-03-20", "root-id")
        assert result == "fallback-1"


# ---------------------------------------------------------------------------
# build_folder_path — hierarchy layouts
# ---------------------------------------------------------------------------

class TestBuildFolderPath:
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
        """Custom: {root}/{folder_name}/{date}"""
        from shared.drive_client import build_folder_path

        self._setup_find_or_create(mock_service, {
            "testem": "custom-id",
            "2026-03-20": "date-id",
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
        assert folder_id == "date-id"
        assert path == "testem/2026-03-20"

    def test_custom_folder_flat(self, mock_service):
        """Custom flat: {root}/{folder_name}"""
        from shared.drive_client import build_folder_path

        self._setup_find_or_create(mock_service, {"testem": "custom-id"})

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
        assert folder_id == "custom-id"
        assert path == "testem"


# ---------------------------------------------------------------------------
# upload_or_replace
# ---------------------------------------------------------------------------

class TestUploadOrReplace:
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

    def test_ads_api_always_json(self):
        from shared.drive_client import infer_report_format

        ext, mime = infer_report_format("ads_api", "SP_TRAFFIC")
        assert ext == ".json"
        assert mime == "application/json"
