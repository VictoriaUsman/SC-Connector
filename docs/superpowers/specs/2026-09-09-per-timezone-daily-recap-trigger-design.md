# Per-timezone daily_recap trigger — design

**Date:** 2026-09-09
**Status:** Approved for planning
**Author:** Claude (with Ian)

## Problem

`daily_recap` fires once a day for every client, at a single fixed UTC
time (`0 23 * * *`, see `infra/resources/scheduler.py`). Each client's
recap reports "the previous full calendar day" in *their own*
`client_timezone` (`_previous_calendar_day` in
`functions/daily_recap/main.py`), but the *trigger* itself doesn't
account for timezone — a single 23:00 UTC fire covers US/Pacific, DE,
UK, and any future AU/SG clients alike.

For a Pacific client, 23:00 UTC is 4 PM local — the recap doesn't land
until the client's day has been over for ~16 hours. For a client in a
timezone further ahead of UTC (e.g., DE/Berlin, UTC+1/+2), the lag from
their own local midnight to 23:00 UTC is even longer. The fixed time
exists specifically to give that day's orders/ads reports time to
finish ingesting (see the handler's own comment on why it "is
scheduled to run after the day's report pulls have ingested") — but it
was sized for the worst case, not measured.

### Evidence the buffer is oversized

On 2026-09-08, itsbodily's Sept 7 recap was generated manually at
07:32–07:43 UTC — only 32–43 minutes after Pacific midnight (Sept 7
ending) — and produced numbers (Spend $932.53, PPC Sales $4,052.57,
Total Sales $7,556.51) identical to the real scheduled run 15+ hours
later at 23:00 UTC. Nothing changed in between: for this client on
this day, that day's data was fully settled well under an hour after
its own midnight, not 16 hours later.

This is one client, one day — not proof every client's ingestion is
always this fast — but it's enough to justify a much shorter buffer
than the current schedule assumes, with a design that stays safe if a
particular day's ingestion runs slower.

## Goal

Each client's recap fires shortly after **their own** local midnight,
not a single shared UTC time — while staying safe against: a slow
ingestion day (data not ready when the window opens), double-sends,
and DST transitions.

## Approach

**One frequently-polling Cloud Scheduler job + runtime, per-client
gating in the handler** — not one Scheduler job per timezone.

Rejected alternative: a dedicated Cloud Scheduler job per distinct
timezone (each using GCP's native IANA-timezone-aware cron, which
handles DST automatically). Rejected because every new client in a
timezone not yet covered would require a human to notice and
provision a new Scheduler job — an easy-to-forget manual infra step.
The polling approach is entirely data-driven off each client's existing
`client_timezone` field: adding a client in a new timezone needs zero
infra changes.

## Design

### 1. Trigger window

- **Buffer:** 1 hour past a client's local midnight (down from the
  current ~16h gap, per the evidence above).
- **Window width:** 1–3 hours past midnight (a 2-hour span), not a
  single instant. This absorbs two things: a poll landing a bit early
  or late relative to the exact hour boundary, and a DST "spring
  forward" transition (which skips exactly one hour of wall-clock
  time) — a narrow single-instant check could miss a client entirely
  on that one day a year; a 2-hour window can't, because at least one
  poll inside it will still land after the skip.

### 2. Scheduler change

`kalilos-{env}-daily-recap`'s Cloud Scheduler job changes from
`0 23 * * *` (once/day) to every 15 minutes: `*/15 * * * *`, still UTC
(the function does the per-client timezone math, not the scheduler).
This is the existing `kalilos:daily-recap-cron` Pulumi config value
(`infra/resources/scheduler.py:107`) — changing it there keeps the
change in Pulumi state rather than a manual out-of-band `gcloud`
edit like today's session's other fixes.

### 3. Per-client gating logic (`functions/daily_recap/main.py`)

For each enabled client in the existing loop, before doing any
BigQuery work:

```
midnight_local = local now's date, at 00:00, in client_tz
hours_since_midnight = (now.astimezone(client_tz) - midnight_local) / 1h

if not (1 <= hours_since_midnight < 3):
    skip (not due yet, or window already passed) — no error, no log spam
    continue

if already_sent_today(client_id, recap_date):
    skip (idempotency)
    continue

... existing build-and-send logic, unchanged ...
```

`recap_date` here is still computed by the existing
`_previous_calendar_day` — unchanged. The new logic only decides
*whether this invocation is the one that should act* for this client;
once it decides yes, everything downstream (querying totals, building
blocks, posting to Slack, logging activity) stays exactly as it is
today.

### 4. Idempotency check — new `shared/db.py` helper

```python
def has_bot_activity(bot: str, client_id: str, recap_date: str, status: str = "sent") -> bool:
    """True if a bot_activity row already records this (bot, client, recap_date, status)."""
```

Queries `bot_activity` filtering on the JSON `payload` fields
(`payload->>'bot'`, `payload->>'client_id'`, `payload->>'recap_date'`,
`payload->>'status'`) — the same table and shape `log_bot_activity`
already writes to on every send (`bot: "daily_recap"`, `status:
"sent"`, `recap_date`, `client_id`). No new table, no new column.

### 5. Failure handling

Unchanged from today's behavior, just on a tighter retry cycle: a
failed send (e.g., a transient BigQuery error) writes a `status:
"failed"` activity row, not `"sent"` — so `has_bot_activity(...,
status="sent")` still returns `False`, and the very next 15-minute
poll inside the same window retries automatically. If the entire
1–3h window passes without a successful send, that client's recap for
that day is skipped until the next day — the same "eventually gives
up" behavior as today, just discovered sooner (within ~2 hours instead
of never retrying at all after the single daily attempt fails). This
is a strict improvement over today, not a new risk.

### 6. Testing

Extend `tests/test_daily_recap.py`:

- Window-boundary cases: just before 1h (skip), just inside (send),
  just past 3h (skip).
- Skip-when-already-sent: mock `has_bot_activity` returning `True`,
  assert no BigQuery query or Slack post happens.
- A DST spring-forward day: construct a `now` where the naive
  hour-of-day arithmetic would suggest a client's window doesn't
  exist, confirm the actual `astimezone`-based computation still
  finds a valid send opportunity within the 2-hour span.
- Multi-client, mixed-timezone: one client due, one not, in the same
  invocation — confirm only the due one sends.

## Out of scope

- Changing the 1–3h window to be configurable per-client (a client
  with unusually slow ingestion isn't a known problem yet — revisit
  if one surfaces).
- Retiring `_previous_calendar_day`'s existing computation — unchanged.
- The hourly bot's own scheduling (`event-report-scheduler`,
  `hourly-bot`) — untouched by this change.
