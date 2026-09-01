"""Tests for shared.db — Postgres (Supabase) operations replacing firestore_utils."""

from __future__ import annotations

import os
import sys
import uuid
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))

from shared import db


class _FakeCursor:
    """Each entry in `responses` is (description, rows_or_None) for one execute() call,
    in call order. `description` is a list of (name,) tuples (only [0] is read by
    _row_to_dict, matching the real cursor.description shape)."""

    def __init__(self, responses):
        self._responses = responses
        self._call_index = 0
        self.queries: list[tuple] = []
        self.description = None
        self._current_rows = None
        self.encoding = "UTF-8"  # Make compatible with psycopg2.sql.as_string()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, params=None):
        self.queries.append((query, params))
        self.description, self._current_rows = self._responses[self._call_index]
        self._call_index += 1

    def fetchone(self):
        return self._current_rows[0] if self._current_rows else None

    def fetchall(self):
        return self._current_rows or []


class _FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def _desc(*names):
    return [(n,) for n in names]


class TestRowToDict:
    def test_plain_columns(self):
        cur = _FakeCursor([(_desc("id", "name"), [("c1", "Acme")])])
        cur.execute("SELECT id, name FROM clients")
        row = cur.fetchone()
        assert db._row_to_dict(cur, row) == {"id": "c1", "name": "Acme"}

    def test_uuid_column_becomes_str(self):
        u = uuid.uuid4()
        cur = _FakeCursor([(_desc("id"), [(u,)])])
        cur.execute("SELECT id FROM schedules")
        row = cur.fetchone()
        result = db._row_to_dict(cur, row)
        assert result == {"id": str(u)}
        assert isinstance(result["id"], str)

    def test_date_column_becomes_isoformat_string(self):
        cur = _FakeCursor([(_desc("execution_date"), [(date(2026, 3, 21),)])])
        cur.execute("SELECT execution_date FROM jobs")
        row = cur.fetchone()
        assert db._row_to_dict(cur, row) == {"execution_date": "2026-03-21"}

    def test_timestamptz_column_stays_a_datetime(self):
        dt = datetime(2026, 3, 21, 3, 0, tzinfo=timezone.utc)
        cur = _FakeCursor([(_desc("created_at"), [(dt,)])])
        cur.execute("SELECT created_at FROM clients")
        row = cur.fetchone()
        result = db._row_to_dict(cur, row)
        assert result == {"created_at": dt}
        assert isinstance(result["created_at"], datetime)

    def test_jsonb_dict_passes_through_unchanged(self):
        cur = _FakeCursor([(_desc("schedule_config"), [({"time": "03:00"},)])])
        cur.execute("SELECT schedule_config FROM schedules")
        row = cur.fetchone()
        assert db._row_to_dict(cur, row) == {"schedule_config": {"time": "03:00"}}

    def test_array_column_passes_through_unchanged(self):
        cur = _FakeCursor([(_desc("marketplaces"), [(["US", "CA"],)])])
        cur.execute("SELECT marketplaces FROM clients")
        row = cur.fetchone()
        assert db._row_to_dict(cur, row) == {"marketplaces": ["US", "CA"]}


class TestGetConnection:
    def test_reads_supabase_db_url_and_sets_autocommit(self, monkeypatch):
        db._conn = None
        monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://fake")
        fake_psycopg2 = MagicMock()
        fake_connection = MagicMock(name="fake-connection")
        fake_psycopg2.connect.return_value = fake_connection
        try:
            with patch.dict(sys.modules, {"psycopg2": fake_psycopg2}):
                conn = db._get_connection()
            assert conn is fake_connection
            assert conn.autocommit is True
            fake_psycopg2.connect.assert_called_once_with("postgresql://fake")
        finally:
            db._conn = None

    def test_caches_connection_across_calls(self, monkeypatch):
        db._conn = None
        monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://fake")
        fake_psycopg2 = MagicMock()
        fake_psycopg2.connect.return_value = MagicMock(name="fake-connection")
        try:
            with patch.dict(sys.modules, {"psycopg2": fake_psycopg2}):
                db._get_connection()
                db._get_connection()
            fake_psycopg2.connect.assert_called_once()
        finally:
            db._conn = None


class TestMergeUpsert:
    def test_insert_wraps_only_jsonb_columns(self):
        cur = _FakeCursor([(None, None)])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            db._merge_upsert(
                "schedules", "id", "s1",
                {"schedule_config": {"time": "03:00"}, "marketplaces": ["US", "CA"]},
            )
        query, params = cur.queries[0]
        sql_text = str(query)
        assert "INSERT INTO" in sql_text
        assert "ON CONFLICT" in sql_text
        # jsonb column value must be wrapped (has an adapter, isn't the bare dict)
        from psycopg2.extras import Json as _RealJson  # only needed to check the wrapper type name
        assert params[1].__class__.__name__ in ("Json",) or "Json" in str(type(params[1]))
        # array column value must be passed through as a bare list, not wrapped
        assert params[2] == ["US", "CA"]

    def test_update_only_touches_columns_present_in_data(self):
        cur = _FakeCursor([(None, None)])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            db._merge_upsert("clients", "id", "c1", {"sp_api_secret_name": "kalilos-staging-sp-api-c1"})
        query, params = cur.queries[0]
        sql_text = str(query)
        assert "sp_api_secret_name" in sql_text
        assert "created_at" not in sql_text  # never touched — not in `data`

    def test_key_column_in_data_is_not_duplicated(self):
        cur = _FakeCursor([(None, None)])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            db._merge_upsert("bot_configs", "client_id", "c1", {"client_id": "c1", "slack_channel_id": "C123"})
        query, params = cur.queries[0]
        sql_text = str(query)
        assert sql_text.count("client_id") == 2  # one INSERT column + one ON CONFLICT target, never a 3rd

    def test_empty_data_update_only_is_a_no_op(self):
        with patch.object(db, "_get_connection") as fake_conn:
            db._merge_upsert_update_only("schedules", "id", "s1", {})
        fake_conn.assert_not_called()  # never even opens a connection for nothing to write

    def test_merge_upsert_with_only_key_column_in_data_does_not_produce_empty_set(self):
        cur = _FakeCursor([(None, None)])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            db._merge_upsert("bot_configs", "client_id", "c1", {"client_id": "c1"})
        query, params = cur.queries[0]
        sql_text = str(query)
        # Implementation choice: with nothing left in `data` after stripping the
        # key column, the ON CONFLICT clause becomes DO NOTHING rather than an
        # empty DO UPDATE SET (which would be invalid SQL).
        assert "DO NOTHING" in sql_text
        assert "DO UPDATE SET" not in sql_text


class TestGetClient:
    def test_found(self):
        cur = _FakeCursor([(_desc("id", "name", "is_active"), [("c1", "Acme", True)])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            result = db.get_client("c1")
        assert result == {"id": "c1", "name": "Acme", "is_active": True}
        query, params = cur.queries[0]
        assert params == ("c1",)

    def test_not_found(self):
        cur = _FakeCursor([(_desc("id"), [])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            assert db.get_client("missing") is None


class TestListClients:
    def test_all(self):
        cur = _FakeCursor([(_desc("id", "name"), [("c1", "Acme"), ("c2", "Globex")])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            result = db.list_clients()
        assert result == [{"id": "c1", "name": "Acme"}, {"id": "c2", "name": "Globex"}]
        query, params = cur.queries[0]
        sql_text = str(query)
        assert "is_active" not in sql_text

    def test_active_only(self):
        cur = _FakeCursor([(_desc("id"), [("c1",)])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            db.list_clients(active_only=True)
        query, params = cur.queries[0]
        assert "is_active" in str(query)


class TestUpsertClient:
    def test_sets_updated_at_and_delegates_to_merge_upsert(self):
        with patch.object(db, "_merge_upsert") as fake_merge:
            db.upsert_client("c1", {"name": "Acme"})
        fake_merge.assert_called_once()
        args, kwargs = fake_merge.call_args
        assert args[0] == "clients"
        assert args[1] == "id"
        assert args[2] == "c1"
        assert args[3]["name"] == "Acme"
        assert "updated_at" in args[3]
        assert isinstance(args[3]["updated_at"], datetime)


class TestDeleteClient:
    def test_deletes_by_id(self):
        cur = _FakeCursor([(None, None)])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            db.delete_client("c1")
        query, params = cur.queries[0]
        assert "DELETE FROM clients" in str(query)
        assert params == ("c1",)


class TestResolveClient:
    def test_exact_id_match(self):
        with patch.object(db, "get_client", return_value={"id": "c1", "name": "Acme"}):
            assert db.resolve_client("c1") == {"id": "c1", "name": "Acme"}

    def test_case_insensitive_id_fallback(self):
        with patch.object(db, "get_client", return_value=None), \
             patch.object(db, "list_clients", return_value=[{"id": "C1", "name": "Acme"}]):
            assert db.resolve_client("c1") == {"id": "C1", "name": "Acme"}

    def test_ambiguous_name_match_returns_none(self):
        clients = [{"id": "c1", "name": "Acme"}, {"id": "c2", "name": "acme"}]
        with patch.object(db, "get_client", return_value=None), \
             patch.object(db, "list_clients", return_value=clients):
            assert db.resolve_client("Acme") is None

    def test_empty_identifier_returns_none(self):
        assert db.resolve_client("") is None


class TestGetSchedule:
    def test_found(self):
        cur = _FakeCursor([(_desc("id", "api_source"), [("s1", "sp_api")])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            assert db.get_schedule("s1") == {"id": "s1", "api_source": "sp_api"}

    def test_not_found(self):
        cur = _FakeCursor([(_desc("id"), [])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            assert db.get_schedule("missing") is None


class TestListSchedules:
    def test_no_filters(self):
        cur = _FakeCursor([(_desc("id"), [("s1",)])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            result = db.list_schedules()
        assert result == [{"id": "s1"}]
        query, params = cur.queries[0]
        assert "WHERE" not in str(query)

    def test_client_id_filter_uses_array_contains(self):
        cur = _FakeCursor([(_desc("id"), [("s1",)])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            db.list_schedules(client_id="c1")
        query, params = cur.queries[0]
        sql_text = str(query)
        assert "client_ids" in sql_text and "@>" in sql_text
        assert params == (["c1"],)

    def test_active_only_filter(self):
        cur = _FakeCursor([(_desc("id"), [("s1",)])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            db.list_schedules(active_only=True)
        query, params = cur.queries[0]
        assert "is_active" in str(query)

    def test_both_filters_combine_with_and(self):
        cur = _FakeCursor([(_desc("id"), [])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            db.list_schedules(client_id="c1", active_only=True)
        query, params = cur.queries[0]
        sql_text = str(query)
        assert "client_ids" in sql_text and "is_active" in sql_text and "AND" in sql_text.upper()
        assert params == (["c1"],)


class TestListDueSchedules:
    def test_filters_active_and_due(self):
        now = datetime(2026, 3, 21, 3, 0, tzinfo=timezone.utc)
        cur = _FakeCursor([(_desc("id"), [("s1",)])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            result = db.list_due_schedules(now)
        assert result == [{"id": "s1"}]
        query, params = cur.queries[0]
        sql_text = str(query)
        assert "is_active" in sql_text and "next_run_at" in sql_text
        assert params == (now,)

    def test_defaults_to_current_time_when_omitted(self):
        cur = _FakeCursor([(_desc("id"), [])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            db.list_due_schedules()
        query, params = cur.queries[0]
        assert isinstance(params[0], datetime)


class TestCreateSchedule:
    def test_defaults_is_active_and_created_at_then_inserts(self):
        cur = _FakeCursor([(_desc("id"), [("new-uuid",)])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            result = db.create_schedule({"api_source": "sp_api", "frequency": "daily"})
        assert result == "new-uuid"
        query, params = cur.queries[0]
        sql_text = str(query)  # psycopg2.sql.Composable has no as_string() without a connection/cursor; str() falls back to repr(), which is sufficient to check which identifiers/clauses were included
        assert "INSERT INTO" in sql_text and "schedules" in sql_text
        assert "RETURNING" in sql_text.upper()

    def test_caller_supplied_is_active_is_not_overwritten(self):
        cur = _FakeCursor([(_desc("id"), [("s1",)])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            db.create_schedule({"api_source": "sp_api", "frequency": "daily", "is_active": False})
        _, params = cur.queries[0]
        assert False in params


class TestUpdateSchedule:
    def test_updates_only_given_columns(self):
        cur = _FakeCursor([(None, None)])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            db.update_schedule("s1", {"is_active": False})
        query, params = cur.queries[0]
        sql_text = str(query)  # psycopg2.sql.Composable has no as_string() without a connection/cursor; str() falls back to repr(), which is sufficient to check which identifiers/clauses were included
        assert "UPDATE schedules" in sql_text or "UPDATE" in sql_text
        assert "is_active" in sql_text
        assert "s1" in params


class TestUpdateScheduleRunTimes:
    def test_sets_both_timestamps(self):
        cur = _FakeCursor([(None, None)])
        last = datetime(2026, 3, 20, 3, 0, tzinfo=timezone.utc)
        nxt = datetime(2026, 3, 21, 3, 0, tzinfo=timezone.utc)
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            db.update_schedule_run_times("s1", last, nxt)
        query, params = cur.queries[0]
        sql_text = str(query)
        assert "last_run_at" in sql_text and "next_run_at" in sql_text
        assert last in params and nxt in params and "s1" in params


class TestClaimDueSchedule:
    def test_claims_when_row_returned(self):
        now = datetime(2026, 3, 21, 3, 0, tzinfo=timezone.utc)
        nxt = datetime(2026, 3, 22, 3, 0, tzinfo=timezone.utc)
        cur = _FakeCursor([(_desc("id"), [("s1",)])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            claimed = db.claim_due_schedule("s1", now, nxt)
        assert claimed is True
        query, params = cur.queries[0]
        sql_text = str(query)  # psycopg2.sql.Composable has no as_string() without a connection/cursor; str() falls back to repr(), which is sufficient to check which identifiers/clauses were included
        assert "UPDATE" in sql_text.upper() and "RETURNING" in sql_text.upper()
        assert "is_active" in sql_text and "next_run_at" in sql_text

    def test_does_not_claim_when_no_row_returned(self):
        now = datetime(2026, 3, 21, 3, 0, tzinfo=timezone.utc)
        nxt = datetime(2026, 3, 22, 3, 0, tzinfo=timezone.utc)
        cur = _FakeCursor([(_desc("id"), [])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            claimed = db.claim_due_schedule("s1", now, nxt)
        assert claimed is False

    def test_exception_is_swallowed_and_returns_false(self):
        now = datetime(2026, 3, 21, 3, 0, tzinfo=timezone.utc)
        nxt = datetime(2026, 3, 22, 3, 0, tzinfo=timezone.utc)
        with patch.object(db, "_get_connection", side_effect=RuntimeError("boom")):
            assert db.claim_due_schedule("s1", now, nxt) is False


class TestDeleteSchedule:
    def test_deletes_by_id(self):
        cur = _FakeCursor([(None, None)])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            db.delete_schedule("s1")
        query, params = cur.queries[0]
        assert "DELETE FROM schedules" in str(query)
        assert params == ("s1",)


class TestCreateJob:
    def test_defaults_and_inserts(self):
        cur = _FakeCursor([(_desc("id"), [("job-uuid",)])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            result = db.create_job({"client_id": "c1"})
        assert result == "job-uuid"
        query, params = cur.queries[0]
        sql_text = str(query)  # psycopg2.sql.Composable has no as_string() without a connection/cursor; str() falls back to repr(), which is sufficient to check which identifiers/clauses were included
        assert "INSERT INTO jobs" in sql_text
        assert "RETURNING" in sql_text.upper()

    def test_caller_supplied_status_is_not_overwritten(self):
        cur = _FakeCursor([(_desc("id"), [("job-uuid",)])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            db.create_job({"client_id": "c1", "status": "running"})
        _, params = cur.queries[0]
        assert "running" in params
        assert "pending" not in params


class TestTryClaimJobLaunch:
    def test_new_key_claims(self):
        cur = _FakeCursor([(_desc("dedupe_key"), [("k1",)])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            assert db.try_claim_job_launch("k1") is True
        query, params = cur.queries[0]
        sql_text = str(query)  # psycopg2.sql.Composable has no as_string() without a connection/cursor; str() falls back to repr(), which is sufficient to check which identifiers/clauses were included
        assert "INSERT INTO job_launch_dedupe" in sql_text
        assert "ON CONFLICT" in sql_text.upper() and "DO NOTHING" in sql_text.upper()

    def test_duplicate_key_does_not_claim(self):
        cur = _FakeCursor([(_desc("dedupe_key"), [])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            assert db.try_claim_job_launch("k1") is False

    def test_fails_open_on_exception(self):
        with patch.object(db, "_get_connection", side_effect=RuntimeError("boom")):
            assert db.try_claim_job_launch("k1") is True

    def test_metadata_is_passed_as_jsonb(self):
        cur = _FakeCursor([(_desc("dedupe_key"), [("k1",)])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            db.try_claim_job_launch("k1", metadata={"schedule_id": "s1"})
        _, params = cur.queries[0]
        assert any("Json" in str(type(p)) for p in params)


class TestGetJob:
    def test_found(self):
        cur = _FakeCursor([(_desc("id", "status"), [("j1", "pending")])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            assert db.get_job("j1") == {"id": "j1", "status": "pending"}

    def test_not_found(self):
        cur = _FakeCursor([(_desc("id"), [])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            assert db.get_job("missing") is None


class TestUpdateJob:
    def test_updates_given_columns(self):
        cur = _FakeCursor([(None, None)])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            db.update_job("j1", {"retry_count": 1})
        query, params = cur.queries[0]
        sql_text = str(query)  # psycopg2.sql.Composable has no as_string() without a connection/cursor; str() falls back to repr(), which is sufficient to check which identifiers/clauses were included
        assert "UPDATE jobs" in sql_text or "jobs" in sql_text
        assert "retry_count" in sql_text


class TestUpdateJobStatus:
    def test_non_terminal_status_does_not_set_completed_at_or_touch_schedule(self):
        cur = _FakeCursor([(None, None)])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)), \
             patch.object(db, "_maybe_update_schedule_run_status") as fake_agg:
            db.update_job_status("j1", "running")
        _, params = cur.queries[0]
        assert "running" in params
        fake_agg.assert_not_called()

    def test_terminal_status_sets_completed_at_and_triggers_aggregation(self):
        cur = _FakeCursor([(None, None)])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)), \
             patch.object(db, "_maybe_update_schedule_run_status") as fake_agg:
            db.update_job_status("j1", "completed")
        query, params = cur.queries[0]
        sql_text = str(query)
        assert "completed_at" in sql_text
        fake_agg.assert_called_once_with("j1")

    def test_extra_kwargs_are_included_in_the_update(self):
        cur = _FakeCursor([(None, None)])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)), \
             patch.object(db, "_maybe_update_schedule_run_status"):
            db.update_job_status("j1", "failed", error_details={"code": "TIMEOUT"})
        _, params = cur.queries[0]
        assert any("Json" in str(type(p)) for p in params)


class TestMaybeUpdateScheduleRunStatus:
    def test_no_op_when_job_missing(self):
        with patch.object(db, "get_job", return_value=None), \
             patch.object(db, "update_schedule") as fake_update:
            db._maybe_update_schedule_run_status("j1")
        fake_update.assert_not_called()

    def test_no_op_when_not_all_siblings_terminal(self):
        job = {"id": "j1", "schedule_id": "s1", "execution_date": "2026-03-21"}
        siblings = [{"status": "completed"}, {"status": "pending"}]
        cur = _FakeCursor([(_desc("status", "gdrive_folder_id"), [("completed", "f1"), ("pending", None)])])
        with patch.object(db, "get_job", return_value=job), \
             patch.object(db, "_get_connection", return_value=_FakeConnection(cur)), \
             patch.object(db, "update_schedule") as fake_update:
            db._maybe_update_schedule_run_status("j1")
        fake_update.assert_not_called()

    def test_all_completed_writes_success_aggregate_and_folder_id(self):
        job = {"id": "j1", "schedule_id": "s1", "execution_date": "2026-03-21"}
        cur = _FakeCursor([
            (_desc("status", "gdrive_folder_id"), [("completed", "f1"), ("completed", None)]),
        ])
        with patch.object(db, "get_job", return_value=job), \
             patch.object(db, "_get_connection", return_value=_FakeConnection(cur)), \
             patch.object(db, "update_schedule") as fake_update:
            db._maybe_update_schedule_run_status("j1")
        fake_update.assert_called_once()
        args, kwargs = fake_update.call_args
        assert args[0] == "s1"
        assert args[1]["last_run_status"] == "success"
        assert args[1]["last_drive_folder_id"] == "f1"
        assert args[1]["last_run_job_count"] == {"completed": 2, "failed": 0, "total": 2}

    def test_mixed_completed_and_failed_writes_partial(self):
        job = {"id": "j1", "schedule_id": "s1", "execution_date": "2026-03-21"}
        cur = _FakeCursor([
            (_desc("status", "gdrive_folder_id"), [("completed", "f1"), ("failed", None)]),
        ])
        with patch.object(db, "get_job", return_value=job), \
             patch.object(db, "_get_connection", return_value=_FakeConnection(cur)), \
             patch.object(db, "update_schedule") as fake_update:
            db._maybe_update_schedule_run_status("j1")
        args, kwargs = fake_update.call_args
        assert args[1]["last_run_status"] == "partial"

    def test_all_failed_writes_failed_and_no_folder_id(self):
        job = {"id": "j1", "schedule_id": "s1", "execution_date": "2026-03-21"}
        cur = _FakeCursor([
            (_desc("status", "gdrive_folder_id"), [("failed", None), ("failed", None)]),
        ])
        with patch.object(db, "get_job", return_value=job), \
             patch.object(db, "_get_connection", return_value=_FakeConnection(cur)), \
             patch.object(db, "update_schedule") as fake_update:
            db._maybe_update_schedule_run_status("j1")
        args, kwargs = fake_update.call_args
        assert args[1]["last_run_status"] == "failed"
        assert "last_drive_folder_id" not in args[1]

    def test_update_schedule_exception_is_swallowed(self):
        job = {"id": "j1", "schedule_id": "s1", "execution_date": "2026-03-21"}
        cur = _FakeCursor([
            (_desc("status", "gdrive_folder_id"), [("completed", "f1")]),
        ])
        with patch.object(db, "get_job", return_value=job), \
             patch.object(db, "_get_connection", return_value=_FakeConnection(cur)), \
             patch.object(db, "update_schedule", side_effect=RuntimeError("boom")):
            db._maybe_update_schedule_run_status("j1")  # must not raise


class TestListJobs:
    def test_no_filters_orders_and_limits(self):
        cur = _FakeCursor([(_desc("id"), [("j1",)])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            result = db.list_jobs()
        assert result == [{"id": "j1"}]
        query, params = cur.queries[0]
        sql_text = str(query)
        assert "ORDER BY started_at DESC" in sql_text
        assert "LIMIT" in sql_text
        assert params[-1] == 50

    def test_all_filters_combine(self):
        cur = _FakeCursor([(_desc("id"), [])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            db.list_jobs(client_id="c1", status="failed", schedule_id="s1", execution_date="2026-03-21", limit=10)
        query, params = cur.queries[0]
        sql_text = str(query)
        assert all(k in sql_text for k in ("schedule_id", "execution_date", "client_id", "status"))
        assert params == ("s1", "2026-03-21", "c1", "failed", 10)


class TestIncrementJobPollCount:
    def test_increments_via_sql(self):
        cur = _FakeCursor([(None, None)])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            db.increment_job_poll_count("j1")
        query, params = cur.queries[0]
        sql_text = str(query)
        assert "poll_count = poll_count + 1" in sql_text
        assert params == ("j1",)


class TestGetEvent:
    def test_found(self):
        cur = _FakeCursor([(_desc("id", "name"), [("e1", "Prime Day")])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            assert db.get_event("e1") == {"id": "e1", "name": "Prime Day"}

    def test_not_found(self):
        cur = _FakeCursor([(_desc("id"), [])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            assert db.get_event("missing") is None


class TestListEvents:
    def test_orders_by_start_date(self):
        cur = _FakeCursor([(_desc("id"), [("e1",), ("e2",)])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            result = db.list_events()
        assert result == [{"id": "e1"}, {"id": "e2"}]
        query, params = cur.queries[0]
        assert "ORDER BY start_date" in str(query)


class TestCreateEvent:
    def test_defaults_and_inserts(self):
        cur = _FakeCursor([(_desc("id"), [("e1",)])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            result = db.create_event({"name": "Prime Day", "start_date": "2026-07-08", "end_date": "2026-07-09"})
        assert result == "e1"
        _, params = cur.queries[0]
        assert "upcoming" in params
        assert False in params  # manually_activated default


class TestUpdateEvent:
    def test_sets_updated_at_and_updates_columns(self):
        cur = _FakeCursor([(None, None)])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            db.update_event("e1", {"status": "live"})
        query, params = cur.queries[0]
        sql_text = str(query)
        assert "status" in sql_text and "updated_at" in sql_text


class TestDeleteEvent:
    def test_deletes_by_id(self):
        cur = _FakeCursor([(None, None)])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            db.delete_event("e1")
        query, params = cur.queries[0]
        assert "DELETE FROM events" in str(query)
        assert params == ("e1",)


class TestGetLiveEvent:
    def test_none_live(self):
        cur = _FakeCursor([(_desc("id"), [])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            assert db.get_live_event() is None

    def test_single_live_event(self):
        cur = _FakeCursor([(_desc("id", "start_date", "name"), [("e1", date(2026, 7, 8), "Prime Day")])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            result = db.get_live_event()
        assert result["id"] == "e1"

    def test_multiple_live_events_picks_deterministically_and_warns(self, caplog):
        cur = _FakeCursor([(_desc("id", "start_date", "name"), [
            ("e2", date(2026, 7, 9), "Later"),
            ("e1", date(2026, 7, 8), "Earlier"),
        ])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            with caplog.at_level("WARNING"):
                result = db.get_live_event()
        assert result["id"] == "e1"  # earliest start_date wins
        # Verify WARNING was actually logged with expected extra fields
        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert record.levelname == "WARNING"
        assert record.live_event_count == 2
        assert record.selected_event_id == "e1"
        assert record.live_event_ids == ["e1", "e2"]  # sorted by start_date then id
        assert record.live_event_names == ["Earlier", "Later"]  # sorted by start_date then id


class TestGetBotConfig:
    def test_found(self):
        cur = _FakeCursor([(_desc("client_id", "slack_channel_id"), [("c1", "C123")])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            assert db.get_bot_config("c1") == {"client_id": "c1", "slack_channel_id": "C123"}

    def test_not_found(self):
        cur = _FakeCursor([(_desc("client_id"), [])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            assert db.get_bot_config("missing") is None


class TestListBotConfigs:
    def test_all(self):
        cur = _FakeCursor([(_desc("client_id"), [("c1",), ("c2",)])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            result = db.list_bot_configs()
        assert result == [{"client_id": "c1"}, {"client_id": "c2"}]


class TestUpsertBotConfig:
    def test_sets_updated_at_and_delegates_to_merge_upsert(self):
        with patch.object(db, "_merge_upsert") as fake_merge:
            db.upsert_bot_config("c1", {"slack_channel_id": "C123"})
        args, kwargs = fake_merge.call_args
        assert args[0] == "bot_configs"
        assert args[1] == "client_id"
        assert args[2] == "c1"
        assert "updated_at" in args[3]


class TestLogBotActivity:
    def test_defaults_timestamp_and_inserts(self):
        cur = _FakeCursor([(_desc("id"), [("a1",)])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            result = db.log_bot_activity({"type": "hourly_post"})
        assert result == "a1"
        query, params = cur.queries[0]
        sql_text = str(query)  # psycopg2.sql.Composable has no as_string() without a connection/cursor; str() falls back to repr(), which is sufficient to check which identifiers/clauses were included
        assert "INSERT INTO bot_activity" in sql_text
        assert any("Json" in str(type(p)) for p in params)  # payload wrapped


class TestThreadAnchors:
    def test_get_returns_none_when_absent(self):
        cur = _FakeCursor([(_desc("parent_ts"), [])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            assert db.get_thread_anchor_ts("e1", "C123", "2026-07-08") is None

    def test_get_returns_stored_ts(self):
        cur = _FakeCursor([(_desc("parent_ts"), [("1234.5678",)])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            assert db.get_thread_anchor_ts("e1", "C123", "2026-07-08") == "1234.5678"

    def test_set_uses_deterministic_id_and_insert_on_conflict_do_nothing(self):
        cur = _FakeCursor([(_desc("id"), [("e1__C123__2026-07-08",)])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            db.set_thread_anchor_ts("e1", "C123", "2026-07-08", "1234.5678", created_by_client_id="c1")
        query, params = cur.queries[0]
        sql_text = str(query)  # psycopg2.sql.Composable has no as_string() without a connection/cursor; str() falls back to repr(), which is sufficient to check which identifiers/clauses were included
        assert "INSERT INTO slack_thread_anchors" in sql_text
        assert "ON CONFLICT" in sql_text.upper() and "DO NOTHING" in sql_text.upper()
        assert "e1__C123__2026-07-08" in params

    def test_set_replaces_slash_in_id(self):
        cur = _FakeCursor([(_desc("id"), [("x",)])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            db.set_thread_anchor_ts("e1/x", "C123", "2026-07-08", "1234.5678")
        _, params = cur.queries[0]
        assert "e1_x__C123__2026-07-08" in params


class TestDriveFolderLocks:
    def test_try_claim_new_lock_succeeds(self):
        cur = _FakeCursor([(_desc("lock_key"), [("p1__Reports",)])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            assert db.try_claim_drive_folder_lock("p1__Reports") is True
        query, params = cur.queries[0]
        sql_text = str(query)  # psycopg2.sql.Composable has no as_string() without a connection/cursor; str() falls back to repr(), which is sufficient to check which identifiers/clauses were included
        assert "INSERT INTO drive_folder_locks" in sql_text
        assert "ON CONFLICT" in sql_text.upper() and "DO NOTHING" in sql_text.upper()

    def test_try_claim_existing_lock_fails(self):
        cur = _FakeCursor([(_desc("lock_key"), [])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            assert db.try_claim_drive_folder_lock("p1__Reports") is False

    def test_get_lock_found(self):
        cur = _FakeCursor([(_desc("lock_key", "folder_id"), [("p1__Reports", "f1")])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            result = db.get_drive_folder_lock("p1__Reports")
        assert result == {"lock_key": "p1__Reports", "folder_id": "f1"}

    def test_get_lock_missing(self):
        cur = _FakeCursor([(_desc("lock_key"), [])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            assert db.get_drive_folder_lock("missing") is None

    def test_set_folder_id_updates_the_row(self):
        cur = _FakeCursor([(None, None)])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            db.set_drive_folder_lock_folder_id("p1__Reports", "f1")
        query, params = cur.queries[0]
        assert "UPDATE drive_folder_locks" in str(query)
        assert params == ("f1", "p1__Reports")

    def test_delete_lock(self):
        cur = _FakeCursor([(None, None)])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            db.delete_drive_folder_lock("p1__Reports")
        query, params = cur.queries[0]
        assert "DELETE FROM drive_folder_locks" in str(query)
        assert params == ("p1__Reports",)


class TestDriveFileIndex:
    def test_get_recorded_file_found(self):
        cur = _FakeCursor([(_desc("file_key", "file_id"), [("f1__report.tsv", "gdrive-123")])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            result = db.get_recorded_drive_file("f1", "report.tsv")
        assert result == {"file_key": "f1__report.tsv", "file_id": "gdrive-123"}
        _, params = cur.queries[0]
        assert params == ("f1__report.tsv",)

    def test_get_recorded_file_missing(self):
        cur = _FakeCursor([(_desc("file_key"), [])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            assert db.get_recorded_drive_file("f1", "report.tsv") is None

    def test_get_recorded_file_replaces_slash_in_key(self):
        cur = _FakeCursor([(_desc("file_key"), [])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            db.get_recorded_drive_file("f1", "a/b.tsv")
        _, params = cur.queries[0]
        assert params == ("f1__a_b.tsv",)

    def test_record_uploaded_file_upserts(self):
        cur = _FakeCursor([(None, None)])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            db.record_uploaded_drive_file("f1", "report.tsv", "gdrive-123")
        query, params = cur.queries[0]
        sql_text = str(query)  # psycopg2.sql.Composable has no as_string() without a connection/cursor; str() falls back to repr(), which is sufficient to check which identifiers/clauses were included
        assert "INSERT INTO drive_file_index" in sql_text
        assert "ON CONFLICT" in sql_text.upper() and "DO UPDATE" in sql_text.upper()
        assert "f1__report.tsv" in params and "gdrive-123" in params


class TestHealthCheck:
    def test_runs_select_1(self):
        cur = _FakeCursor([(None, [(1,)])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            db.health_check()  # must not raise
        query, params = cur.queries[0]
        assert "SELECT 1" in str(query)

    def test_propagates_connection_failure(self):
        with patch.object(db, "_get_connection", side_effect=RuntimeError("down")):
            with pytest.raises(RuntimeError):
                db.health_check()


class TestCountActiveJobs:
    def test_returns_bounded_count(self):
        cur = _FakeCursor([(_desc("count"), [(7,)])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            result = db.count_active_jobs("c1", ["pending", "polling"], limit=11)
        assert result == 7
        query, params = cur.queries[0]
        sql_text = str(query)
        assert "jobs" in sql_text and "LIMIT" in sql_text.upper()
        assert params == ("c1", ["pending", "polling"], 11)

    def test_default_limit_is_eleven(self):
        cur = _FakeCursor([(_desc("count"), [(0,)])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            db.count_active_jobs("c1", ["pending"])
        _, params = cur.queries[0]
        assert params[-1] == 11


class TestOauthState:
    def test_get_found(self):
        cur = _FakeCursor([(_desc("state", "data"), [("s1", {"client_id": "c1"})])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            result = db.get_oauth_state("s1")
        assert result == {"state": "s1", "data": {"client_id": "c1"}}

    def test_get_missing(self):
        cur = _FakeCursor([(_desc("state"), [])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            assert db.get_oauth_state("missing") is None

    def test_save_upserts_and_wraps_data_as_jsonb(self):
        cur = _FakeCursor([(None, None)])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            db.save_oauth_state("s1", {"client_id": "c1", "api_source": "sp_api"})
        query, params = cur.queries[0]
        sql_text = str(query)
        assert "INSERT INTO oauth_states" in sql_text
        assert "ON CONFLICT" in sql_text.upper() and "DO UPDATE" in sql_text.upper()
        assert params[0] == "s1"
        assert any("Json" in str(type(p)) for p in params)

    def test_delete(self):
        cur = _FakeCursor([(None, None)])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            db.delete_oauth_state("s1")
        query, params = cur.queries[0]
        assert "DELETE FROM oauth_states" in str(query)
        assert params == ("s1",)


class TestAppConfig:
    def test_get_found_returns_value_directly(self):
        cur = _FakeCursor([(_desc("value"), [({"rates": {"USD": 1.0}, "base": "USD", "fetched_at": 123.0},)])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            result = db.get_app_config("currency_rates")
        assert result == {"rates": {"USD": 1.0}, "base": "USD", "fetched_at": 123.0}

    def test_get_missing(self):
        cur = _FakeCursor([(_desc("value"), [])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            assert db.get_app_config("missing") is None

    def test_set_upserts_and_wraps_value_as_jsonb(self):
        cur = _FakeCursor([(None, None)])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            db.set_app_config("currency_rates", {"rates": {}, "base": "USD", "fetched_at": 1.0})
        query, params = cur.queries[0]
        sql_text = str(query)
        assert "INSERT INTO app_config" in sql_text
        assert "ON CONFLICT" in sql_text.upper() and "DO UPDATE" in sql_text.upper()
        assert params[0] == "currency_rates"
        assert any("Json" in str(type(p)) for p in params)
