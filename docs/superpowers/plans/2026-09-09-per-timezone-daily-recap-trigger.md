# Per-Timezone daily_recap Trigger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `daily_recap`'s single fixed-UTC-time trigger with a per-client, timezone-aware one, so each client's recap fires shortly (1-3h) after *their own* local midnight instead of a shared 23:00 UTC slot.

**Architecture:** The Cloud Scheduler job that invokes `daily_recap` moves from once/day to every 15 minutes. The function itself gains a gating check at the top of its per-client loop: skip a client unless `now` falls 1-3 hours past their local midnight (`_is_due`), and skip if that day's recap was already sent (`has_bot_activity`, a new read against the existing `bot_activity` log — no new table).

**Tech Stack:** Python 3.12, `zoneinfo` (stdlib), `pytest`, Pulumi (`pulumi_gcp`), Postgres/Supabase via `psycopg2`.

**Spec:** `docs/superpowers/specs/2026-09-09-per-timezone-daily-recap-trigger-design.md`

## Global Constraints

- Trigger window: **1 to 3 hours** (inclusive start, exclusive end) past a client's local midnight, computed from **absolute elapsed time** (aware-datetime subtraction), not wall-clock hour reading — this is what makes the window DST-safe (verified in Task 3).
- Idempotency check: `has_bot_activity("daily_recap", client_id, recap_date, status="sent")` against the existing `bot_activity` table — no schema changes.
- Cloud Scheduler cron: `*/15 * * * *`, `time_zone="UTC"` (unchanged) — the function does all per-client timezone math, not the scheduler.
- `_previous_calendar_day` (recap-date computation) is unchanged by this plan.
- No new Pulumi resources — only the existing `daily_recap` Cloud Scheduler `Job`'s `schedule` value changes.

---

### Task 1: `has_bot_activity` helper in `shared/db.py`

**Files:**
- Modify: `functions/shared/db.py` (add new function after `log_bot_activity`, currently ending at line 768)
- Test: `tests/test_db.py` (add new test class at end of file)

**Interfaces:**
- Consumes: `_get_connection()` (already defined in `db.py`) — same pattern every other query function in this file uses.
- Produces: `has_bot_activity(bot: str, client_id: str, recap_date: str, status: str = "sent") -> bool`, imported by `functions/daily_recap/main.py` in Task 4.

- [ ] **Step 1: Write the failing tests**

Add to the end of `tests/test_db.py`:

```python
class TestHasBotActivity:
    def test_true_when_matching_row_exists(self):
        cur = _FakeCursor([(_desc("exists"), [(1,)])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            result = db.has_bot_activity("daily_recap", "c1", "2026-09-08")
        assert result is True

    def test_false_when_no_matching_row(self):
        cur = _FakeCursor([(_desc("exists"), [])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            result = db.has_bot_activity("daily_recap", "c1", "2026-09-08")
        assert result is False

    def test_queries_bot_client_date_and_status(self):
        cur = _FakeCursor([(_desc("exists"), [])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            db.has_bot_activity("daily_recap", "c1", "2026-09-08", status="sent")
        query, params = cur.queries[-1]
        sql_text = str(query)
        assert "bot_activity" in sql_text
        assert "payload->>'bot'" in sql_text
        assert "payload->>'client_id'" in sql_text
        assert "payload->>'recap_date'" in sql_text
        assert "payload->>'status'" in sql_text
        assert params == ("daily_recap", "c1", "2026-09-08", "sent")

    def test_default_status_is_sent(self):
        cur = _FakeCursor([(_desc("exists"), [])])
        with patch.object(db, "_get_connection", return_value=_FakeConnection(cur)):
            db.has_bot_activity("daily_recap", "c1", "2026-09-08")
        _, params = cur.queries[-1]
        assert params[3] == "sent"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_db.py::TestHasBotActivity -v`
Expected: FAIL with `AttributeError: module 'shared.db' has no attribute 'has_bot_activity'`

- [ ] **Step 3: Write the implementation**

In `functions/shared/db.py`, add immediately after the `log_bot_activity` function (after its closing `return str(row[0])` line):

```python


def has_bot_activity(bot: str, client_id: str, recap_date: str, status: str = "sent") -> bool:
    """True if a bot_activity row already records this (bot, client, recap_date, status).

    Used to make a periodic (rather than once-daily) trigger idempotent: before
    sending, a caller checks whether today's send already happened so a
    more-frequent poll doesn't double-post. Queries the same `payload` shape
    ``log_bot_activity`` already writes on every send (``bot``, ``client_id``,
    ``recap_date``, ``status``) — no new table or column.
    """
    conn = _get_connection()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM bot_activity
            WHERE payload->>'bot' = %s
              AND payload->>'client_id' = %s
              AND payload->>'recap_date' = %s
              AND payload->>'status' = %s
            LIMIT 1
            """,
            (bot, client_id, recap_date, status),
        )
        return cur.fetchone() is not None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_db.py::TestHasBotActivity -v`
Expected: 4 passed

- [ ] **Step 5: Run the full db test file to check for regressions**

Run: `python -m pytest tests/test_db.py -v`
Expected: all passed (no pre-existing test broken)

- [ ] **Step 6: Commit**

```bash
git add functions/shared/db.py tests/test_db.py
git commit -m "Add has_bot_activity helper for idempotent periodic triggers

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: Cloud Scheduler cron change (Pulumi)

**Files:**
- Modify: `infra/resources/scheduler.py:100-108`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: nothing consumed by other tasks (independent, infra-only config value).

- [ ] **Step 1: Update the schedule and its explanatory comment**

In `infra/resources/scheduler.py`, replace:

```python
                # Run after the day's report pulls (ads *and* the orders report)
                # have ingested. At the old 10:00 UTC slot the recap queried
                # before the orders report landed (~10:30–11:00 UTC), so Total
                # Sales read $0 while ads were already correct. 23:00 UTC is still
                # the same Pacific calendar day as the old slot, so the recap's
                # "previous full calendar day" is unchanged — only the data is now
                # present. Overridable via the kalilos:daily-recap-cron config.
                schedule=kalilos_config.get("daily-recap-cron") or "0 23 * * *",
                time_zone="UTC",
```

with:

```python
                # Runs every 15 minutes; the function itself decides, per
                # client, whether "now" falls 1-3 hours past *that client's*
                # own local midnight (see functions/daily_recap/main.py's
                # `_is_due`) before doing any work. This replaced a single
                # fixed 23:00 UTC daily fire — correct for keeping Total Sales
                # from reading $0 before that day's orders report had
                # ingested, but it meant every client waited until 23:00 UTC
                # regardless of their own timezone (up to ~16h after a
                # Pacific client's own midnight). Overridable via the
                # kalilos:daily-recap-cron config.
                schedule=kalilos_config.get("daily-recap-cron") or "*/15 * * * *",
                time_zone="UTC",
```

- [ ] **Step 2: Verify the file still imports cleanly**

Run: `python -c "import ast; ast.parse(open('infra/resources/scheduler.py').read())"`
Expected: no output (parses without error)

- [ ] **Step 3: Commit**

```bash
git add infra/resources/scheduler.py
git commit -m "Change daily_recap Cloud Scheduler cron to every 15 minutes

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: `_hours_since_local_midnight` and `_is_due` in `daily_recap/main.py`

**Files:**
- Modify: `functions/daily_recap/main.py` (add new functions after `_previous_calendar_day`, which currently ends at line 223)
- Test: `tests/test_daily_recap.py` (add new test class)

**Interfaces:**
- Consumes: `ZoneInfo`, `datetime`, `timedelta` — already imported in `daily_recap/main.py`.
- Produces: `_hours_since_local_midnight(now: datetime, client_tz: ZoneInfo) -> float` and `_is_due(now: datetime, client_tz: ZoneInfo) -> bool`, wired into `handler()` in Task 4.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_daily_recap.py` (a new class; place it near `TestPreviousCalendarDay`):

```python
class TestIsDue:
    def test_before_window_not_due(self):
        from daily_recap.main import _is_due

        # Pacific midnight (PDT, UTC-7) on 06/05 is 07:00 UTC. 30 min later
        # is well inside the "not yet" zone (window starts at 1h).
        now = datetime(2026, 6, 5, 7, 30, tzinfo=timezone.utc)
        assert _is_due(now, ZoneInfo("America/Los_Angeles")) is False

    def test_window_start_is_due_inclusive(self):
        from daily_recap.main import _is_due

        # Exactly 1h past Pacific midnight (07:00 UTC -> 08:00 UTC).
        now = datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc)
        assert _is_due(now, ZoneInfo("America/Los_Angeles")) is True

    def test_inside_window_is_due(self):
        from daily_recap.main import _is_due

        # 2h past Pacific midnight.
        now = datetime(2026, 6, 5, 9, 0, tzinfo=timezone.utc)
        assert _is_due(now, ZoneInfo("America/Los_Angeles")) is True

    def test_window_end_not_due_exclusive(self):
        from daily_recap.main import _is_due

        # Exactly 3h past Pacific midnight — window end is exclusive.
        now = datetime(2026, 6, 5, 10, 0, tzinfo=timezone.utc)
        assert _is_due(now, ZoneInfo("America/Los_Angeles")) is False

    def test_after_window_not_due(self):
        from daily_recap.main import _is_due

        # 4h past Pacific midnight.
        now = datetime(2026, 6, 5, 11, 0, tzinfo=timezone.utc)
        assert _is_due(now, ZoneInfo("America/Los_Angeles")) is False

    def test_different_timezone_computed_independently(self):
        from daily_recap.main import _is_due

        # Berlin (CEST, UTC+2) midnight on 06/05 is 05/04 22:00 UTC. 2h later
        # is 06/05 00:00 UTC.
        now = datetime(2026, 6, 5, 0, 0, tzinfo=timezone.utc)
        assert _is_due(now, ZoneInfo("Europe/Berlin")) is True

    def test_dst_spring_forward_still_has_a_due_window(self):
        """US DST begins 2026-03-08: clocks skip 2:00 AM -> 3:00 AM Pacific.
        A window based on absolute elapsed time (not wall-clock hour) must
        still produce a due instant that day, even though the wall clock
        never reads some hours at all.
        """
        from daily_recap.main import _is_due

        # Pacific midnight on 2026-03-08 is still PST (UTC-8) -> 08:00 UTC.
        # 2h of *absolute* elapsed time later is 10:00 UTC, which is exactly
        # the DST transition instant (2:00 AM PST becomes 3:00 AM PDT).
        now = datetime(2026, 3, 8, 10, 0, tzinfo=timezone.utc)
        assert _is_due(now, ZoneInfo("America/Los_Angeles")) is True

    def test_dst_spring_forward_window_still_closes(self):
        from daily_recap.main import _is_due

        # 4h absolute elapsed past the same Pacific midnight.
        now = datetime(2026, 3, 8, 12, 0, tzinfo=timezone.utc)
        assert _is_due(now, ZoneInfo("America/Los_Angeles")) is False


class TestHoursSinceLocalMidnight:
    def test_exact_hours(self):
        from daily_recap.main import _hours_since_local_midnight

        now = datetime(2026, 6, 5, 9, 30, tzinfo=timezone.utc)  # 07:00 UTC = Pacific midnight
        hours = _hours_since_local_midnight(now, ZoneInfo("America/Los_Angeles"))
        assert abs(hours - 2.5) < 0.001

    def test_never_negative_at_midnight_itself(self):
        from daily_recap.main import _hours_since_local_midnight

        now = datetime(2026, 6, 5, 7, 0, tzinfo=timezone.utc)  # exactly Pacific midnight
        hours = _hours_since_local_midnight(now, ZoneInfo("America/Los_Angeles"))
        assert abs(hours - 0.0) < 0.001
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_daily_recap.py::TestIsDue tests/test_daily_recap.py::TestHoursSinceLocalMidnight -v`
Expected: FAIL with `ImportError: cannot import name '_is_due'`

- [ ] **Step 3: Write the implementation**

In `functions/daily_recap/main.py`, add immediately after `_previous_calendar_day`'s closing line (`return now.astimezone(client_tz).date() - timedelta(days=1)`):

```python


# Trigger window: how long after a client's own local midnight the recap is
# allowed to fire. 1h buffer (down from an earlier fixed 23:00 UTC slot that
# gave Pacific clients up to ~16h of lag) — evidence from 2026-09-08 showed a
# client's data fully settled within ~40 minutes of its own midnight. The 2h
# window width (not a single instant) absorbs poll timing slop and, more
# importantly, a DST "spring forward" transition: computed from *absolute*
# elapsed time (aware-datetime subtraction, not a wall-clock hour reading),
# the window still produces a due instant on a day where the wall clock skips
# an hour entirely (see TestIsDue's DST tests).
_TRIGGER_WINDOW_START_HOURS = 1.0
_TRIGGER_WINDOW_END_HOURS = 3.0


def _hours_since_local_midnight(now: datetime, client_tz: ZoneInfo) -> float:
    """Absolute hours elapsed since local midnight today, in client_tz."""
    local_now = now.astimezone(client_tz)
    midnight_local = datetime(local_now.year, local_now.month, local_now.day, tzinfo=client_tz)
    return (local_now - midnight_local).total_seconds() / 3600


def _is_due(now: datetime, client_tz: ZoneInfo) -> bool:
    """True when `now` falls in the post-local-midnight trigger window."""
    hours = _hours_since_local_midnight(now, client_tz)
    return _TRIGGER_WINDOW_START_HOURS <= hours < _TRIGGER_WINDOW_END_HOURS
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_daily_recap.py::TestIsDue tests/test_daily_recap.py::TestHoursSinceLocalMidnight -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add functions/daily_recap/main.py tests/test_daily_recap.py
git commit -m "Add per-timezone trigger window helpers (_is_due)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: Wire gating into `handler()` + update existing tests

**Files:**
- Modify: `functions/daily_recap/main.py:35-39` (imports), `:109-122` (handler loop, insert gating)
- Test: `tests/test_daily_recap.py` (update ~11 existing tests + add new gating-behavior tests)

**Interfaces:**
- Consumes: `has_bot_activity` (Task 1), `_is_due` (Task 3).
- Produces: nothing new consumed elsewhere — this is the integration point.

- [ ] **Step 1: Add the `has_bot_activity` import**

In `functions/daily_recap/main.py`, change:

```python
from shared.db import (
    get_client,
    list_bot_configs,
    log_bot_activity,
)
```

to:

```python
from shared.db import (
    get_client,
    has_bot_activity,
    list_bot_configs,
    log_bot_activity,
)
```

- [ ] **Step 2: Write the failing tests for the new gating behavior**

Add to `tests/test_daily_recap.py` (a new class, placed after `TestMultiMarketplaceRecap`):

```python
class TestTriggerGating:
    def test_client_not_due_yet_is_skipped(self):
        """Outside the 1-3h post-local-midnight window: no query, no send."""
        from daily_recap.main import handler, AccountTotals

        # 30 min past Pacific midnight — before the window opens.
        now = datetime(2026, 6, 5, 7, 30, tzinfo=timezone.utc)

        with (
            patch("daily_recap.main.datetime") as mock_dt,
            patch("daily_recap.main.list_bot_configs", return_value=[_make_bot_config()]),
            patch("daily_recap.main.get_client", return_value={"id": "c1", "name": "Acme", "is_active": True}),
            patch("daily_recap.main._query_account_totals") as mock_query,
            patch("daily_recap.main.has_bot_activity") as mock_has_activity,
            patch("daily_recap.main.post_message") as mock_post,
        ):
            mock_dt.now.return_value = now
            body, status = handler(_make_request())

        assert body["messages_sent"] == 0
        mock_query.assert_not_called()
        mock_post.assert_not_called()
        # Not-due is decided before ever checking activity history.
        mock_has_activity.assert_not_called()

    def test_client_due_but_already_sent_is_skipped(self):
        from daily_recap.main import handler, AccountTotals

        # 2h past Pacific midnight — inside the window.
        now = datetime(2026, 6, 5, 9, 0, tzinfo=timezone.utc)

        with (
            patch("daily_recap.main.datetime") as mock_dt,
            patch("daily_recap.main.list_bot_configs", return_value=[_make_bot_config()]),
            patch("daily_recap.main.get_client", return_value={"id": "c1", "name": "Acme", "is_active": True}),
            patch("daily_recap.main.has_bot_activity", return_value=True) as mock_has_activity,
            patch("daily_recap.main._query_account_totals") as mock_query,
            patch("daily_recap.main.post_message") as mock_post,
        ):
            mock_dt.now.return_value = now
            body, status = handler(_make_request())

        assert body["messages_sent"] == 0
        mock_query.assert_not_called()
        mock_post.assert_not_called()
        mock_has_activity.assert_called_once_with("daily_recap", "c1", "2026-06-04")

    def test_client_due_and_not_yet_sent_proceeds(self):
        from daily_recap.main import handler, AccountTotals

        now = datetime(2026, 6, 5, 9, 0, tzinfo=timezone.utc)

        with (
            patch("daily_recap.main.datetime") as mock_dt,
            patch("daily_recap.main.list_bot_configs", return_value=[_make_bot_config()]),
            patch("daily_recap.main.get_client", return_value={"id": "c1", "name": "Acme", "is_active": True}),
            patch("daily_recap.main.has_bot_activity", return_value=False),
            patch("daily_recap.main._query_account_totals", return_value=AccountTotals(100, 400, 1000)),
            patch("daily_recap.main._query_prior_year_totals", return_value=None),
            patch("daily_recap.main.post_message", return_value={"ok": True, "ts": "1.2"}) as mock_post,
            patch("daily_recap.main.log_bot_activity"),
        ):
            mock_dt.now.return_value = now
            body, status = handler(_make_request())

        assert body["messages_sent"] == 1
        mock_post.assert_called_once()

    def test_mixed_clients_only_due_one_sends(self):
        """Two clients in the same invocation, in different timezones — only
        the one whose local time is inside its own window sends."""
        from daily_recap.main import handler, AccountTotals

        due_config = _make_bot_config(client_id="due-client")
        due_config["client_timezone"] = "America/Los_Angeles"
        due_config["channels"] = [{"id": "C_DUE"}]
        del due_config["slack_channel_id"]

        not_due_config = _make_bot_config(client_id="not-due-client")
        not_due_config["client_timezone"] = "Europe/Berlin"
        not_due_config["channels"] = [{"id": "C_NOT_DUE"}]
        del not_due_config["slack_channel_id"]

        # 2h past Pacific midnight (due) — Berlin midnight was ~11h ago (not due).
        now = datetime(2026, 6, 5, 9, 0, tzinfo=timezone.utc)

        with (
            patch("daily_recap.main.datetime") as mock_dt,
            patch("daily_recap.main.list_bot_configs", return_value=[due_config, not_due_config]),
            patch("daily_recap.main.get_client", return_value={"id": "x", "name": "X", "is_active": True}),
            patch("daily_recap.main.has_bot_activity", return_value=False),
            patch("daily_recap.main._query_account_totals", return_value=AccountTotals(100, 400, 1000)),
            patch("daily_recap.main._query_prior_year_totals", return_value=None),
            patch("daily_recap.main.post_message", return_value={"ok": True, "ts": "1.2"}) as mock_post,
            patch("daily_recap.main.log_bot_activity"),
        ):
            mock_dt.now.return_value = now
            body, status = handler(_make_request())

        assert body["messages_sent"] == 1
        mock_post.assert_called_once()
        assert mock_post.call_args[0][0] == "C_DUE"
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `python -m pytest tests/test_daily_recap.py::TestTriggerGating -v`
Expected: FAIL (`_is_due`/`has_bot_activity` not yet checked in `handler()`, so e.g. `test_client_not_due_yet_is_skipped` fails because `mock_query` WAS called)

- [ ] **Step 4: Wire the gating checks into `handler()`**

In `functions/daily_recap/main.py`, change:

```python
        client_tz = ZoneInfo(config.get("client_timezone", "America/Los_Angeles"))
        recap_date = _previous_calendar_day(now, client_tz)
        currency = config.get("base_currency", "USD")

        try:
            # Always recap the previous full calendar day (the spec's "previous
            # full calendar day") and query that exact day. The recap is
            # scheduled to run after the day's report pulls have ingested (see
            # infra/resources/scheduler.py), so yesterday's ads *and* orders are
            # present by the time this runs. Earlier revisions resolved "the most
            # recent day with any data", but that was dominated by the ads tables
            # (which ingest before the orders report), so the chosen day's orders
            # were still missing and Total Sales read $0 while ads were correct.
            report_date = recap_date.isoformat()
```

to:

```python
        client_tz = ZoneInfo(config.get("client_timezone", "America/Los_Angeles"))
        recap_date = _previous_calendar_day(now, client_tz)
        currency = config.get("base_currency", "USD")

        # This function is invoked every 15 minutes (see
        # infra/resources/scheduler.py); each client only gets acted on once
        # it's inside its own 1-3h-past-local-midnight window, and only if
        # that day's recap hasn't already been sent — two independent gates
        # so a more-frequent trigger can't double-post.
        if not _is_due(now, client_tz):
            continue

        report_date = recap_date.isoformat()
        if has_bot_activity("daily_recap", client_id, report_date):
            continue

        try:
            # Always recap the previous full calendar day (the spec's "previous
            # full calendar day") and query that exact day. The recap is
            # scheduled to run after the day's report pulls have ingested (see
            # infra/resources/scheduler.py), so yesterday's ads *and* orders are
            # present by the time this runs. Earlier revisions resolved "the most
            # recent day with any data", but that was dominated by the ads tables
            # (which ingest before the orders report), so the chosen day's orders
            # were still missing and Total Sales read $0 while ads were correct.
```

(Note: `report_date = recap_date.isoformat()` moved up out of the `try` block — it's needed by the new `has_bot_activity` check before the try starts, and the try block no longer needs to compute it again.)

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `python -m pytest tests/test_daily_recap.py::TestTriggerGating -v`
Expected: 4 passed

- [ ] **Step 6: Run the full test file — expect many pre-existing failures**

Run: `python -m pytest tests/test_daily_recap.py -v`
Expected: FAIL — every pre-existing test that calls `handler()` with a `now` outside the new window, or without mocking `has_bot_activity`, now fails (either skipped-when-it-shouldn't-be, or a real `has_bot_activity` call raising because `SUPABASE_DB_URL` isn't set in the test environment). This is expected — Step 7 fixes each one.

- [ ] **Step 7: Update every pre-existing test that calls `handler()` to bypass gating**

These tests are about other behavior (channel routing, tag blocks, YoY, multi-marketplace, AU end-to-end) — gating is intentionally out of scope for them, so each gets `patch("daily_recap.main._is_due", return_value=True)` and `patch("daily_recap.main.has_bot_activity", return_value=False)` added to its existing `with (...)` block, with no other change.

In `tests/test_daily_recap.py`, apply each of the following exact replacements:

```python
    def test_independent_of_hourly_toggle(self):
        """daily_recap fires for a client even when the hourly bot is disabled."""
        from daily_recap.main import handler, AccountTotals

        with (
            patch("daily_recap.main.list_bot_configs", return_value=[
                _make_bot_config(daily_recap_enabled=True, hourly_enabled=False),
            ]),
            patch("daily_recap.main.get_client", return_value={"id": "c1", "name": "Acme", "is_active": True}),
            patch("daily_recap.main._query_account_totals", return_value=AccountTotals(100, 400, 1000)),
            patch("daily_recap.main._query_prior_year_totals", return_value=None),
            patch("daily_recap.main.post_message", return_value={"ok": True, "ts": "1.2"}) as mock_post,
            patch("daily_recap.main.log_bot_activity"),
        ):
            body, status = handler(_make_request())
```

becomes:

```python
    def test_independent_of_hourly_toggle(self):
        """daily_recap fires for a client even when the hourly bot is disabled."""
        from daily_recap.main import handler, AccountTotals

        with (
            patch("daily_recap.main.list_bot_configs", return_value=[
                _make_bot_config(daily_recap_enabled=True, hourly_enabled=False),
            ]),
            patch("daily_recap.main.get_client", return_value={"id": "c1", "name": "Acme", "is_active": True}),
            patch("daily_recap.main._is_due", return_value=True),
            patch("daily_recap.main.has_bot_activity", return_value=False),
            patch("daily_recap.main._query_account_totals", return_value=AccountTotals(100, 400, 1000)),
            patch("daily_recap.main._query_prior_year_totals", return_value=None),
            patch("daily_recap.main.post_message", return_value={"ok": True, "ts": "1.2"}) as mock_post,
            patch("daily_recap.main.log_bot_activity"),
        ):
            body, status = handler(_make_request())
```

Repeat the same two-line insertion (`patch("daily_recap.main._is_due", return_value=True),` then `patch("daily_recap.main.has_bot_activity", return_value=False),`, placed immediately after each test's `get_client` patch line) for these tests — each `with (...)` block gains exactly those two lines, nothing else changes:

- `TestHandlerDelivery.test_posts_recap_for_enabled_client`
- `TestHandlerDelivery.test_uses_test_channel_when_configured`
- `TestHandlerDelivery.test_tags_configured_channel_users`
- `TestHandlerDelivery.test_no_tag_block_when_channel_has_no_tag_user_ids`
- `TestHandlerDelivery.test_logs_failure`
- `TestRecapDayIsAlwaysPreviousCalendarDay.test_queries_and_labels_yesterday_regardless_of_data_availability`
- `TestRecapDayIsAlwaysPreviousCalendarDay.test_timezone_rollover_singapore`
- `TestAuAccountEndToEnd.test_recap_generated_and_delivered_for_au_account`
- `TestMultiMarketplaceRecap.test_single_marketplace_has_header_but_no_total`
- `TestMultiMarketplaceRecap.test_multi_marketplace_gets_header_per_marketplace_and_total`
- `TestMultiMarketplaceRecap.test_total_yoy_only_when_every_marketplace_has_a_baseline`
- `TestMultiMarketplaceRecap.test_total_yoy_shown_when_all_marketplaces_have_a_baseline`

For `TestHandlerDelivery.test_posts_recap_for_enabled_client` specifically, the `with (...)` block already has a `patch("daily_recap.main.datetime")` line — insert the two new patches immediately after the `get_client` patch, same as every other test (order of patches in a `with (...)` tuple doesn't affect behavior, only readability — keep it consistent with where it's inserted elsewhere).

For `TestAuAccountEndToEnd.test_recap_generated_and_delivered_for_au_account`, insert the two new patches immediately after its `get_client` patch line (before `patch("daily_recap.main._get_bq", return_value=fake)`).

- [ ] **Step 8: Run the full test file again**

Run: `python -m pytest tests/test_daily_recap.py -v`
Expected: all passed

- [ ] **Step 9: Run the full test suite excluding the pre-existing-broken slack_bot file**

Run: `python -m pytest tests/ -q --ignore=tests/test_slack_bot.py`
Expected: all passed (confirms no unrelated regression)

- [ ] **Step 10: Commit**

```bash
git add functions/daily_recap/main.py tests/test_daily_recap.py
git commit -m "Gate daily_recap sends on per-client trigger window + idempotency

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## After implementation

This plan does not include deploying the updated Cloud Function or applying
the Pulumi scheduler change to any live environment — confirm with the user
before deploying, same as every other live-infra change made earlier in this
project (see `docs/superpowers/specs/2026-09-09-per-timezone-daily-recap-trigger-design.md`
for context on why: the repo's working tree carries a lot of unrelated
uncommitted changes, so a blanket `pulumi up` isn't safe to run without
checking first).
