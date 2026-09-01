"""Tests for shared.db — Postgres (Supabase) operations replacing firestore_utils."""

from __future__ import annotations

import os
import sys
import uuid
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

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
