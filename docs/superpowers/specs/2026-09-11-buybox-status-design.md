# Buy Box Status — Design Spec

**Date:** 2026-09-11
**Status:** Approved for planning

## Problem

Kalilos has no way to tell a client whether they currently hold the Buy Box
for their ASINs. The existing `GET_SALES_AND_TRAFFIC_REPORT` has a
`buyBoxPercentage` field, but that's a historical percentage per ASIN per
day — not a live "yes/no, right now" check, which is what was asked for.

## Decisions (from brainstorming)

- **ASIN scope**: every active listing, not a manually curated list.
- **Cadence**: no dedicated always-on schedule for now. Built as a standard
  API operation (`shared.api_operations`), so it's triggerable on-demand
  *and* schedulable via the existing `schedules` table for free — same as
  Replenishment/Finances today. No new Cloud Scheduler job in this phase.
- **Output**: Drive TSV only (the existing on-demand/scheduled delivery
  path). No BigQuery ingestion of the Buy Box results themselves, no Slack
  alerting on Buy Box loss — both explicitly deferred (see Out of Scope).

## Architecture

Two pieces, built on patterns already in the codebase:

### 1. Active-listings ASIN source (reuses an existing, already-pulled report)

`GET_MERCHANT_LISTINGS_ALL_DATA` ("Active Listings") is already pulled for
at least one client today, but was never registered for BigQuery ingestion
— it lands in Drive and nothing else. Register it (same pattern as today's
Orders fix):

- New `TableSchema` in `functions/shared/bq_schemas.py`: `active_listings`
  table, columns `sku` (STRING, from `seller-sku`), `asin` (STRING, from
  `asin1`), `item_name` (STRING), `status` (STRING), `fulfillment_channel`
  (STRING), `price` (FLOAT), `quantity` (INTEGER). `dedup_key=("sku",)` —
  MERGE dedup on SKU, matching the existing dedup-view pattern
  (`*_latest` view via `_latest_view_query`) so a re-pull doesn't duplicate.
- New entry in `_REGISTRY`: `("GET_MERCHANT_LISTINGS_ALL_DATA", "sp_api")`.
- New table definition in `infra/resources/bigquery.py`'s `TABLE_DEFS`
  (mirrors `orders`'s shape, partitioned on `report_date`).

This is purely additive — no existing behavior changes, same as the Orders
registry fix from today.

### 2. Buy Box check (new)

- **`functions/shared/pricing_client.py`** — new client module, same shape
  as `replenishment_client.py`: built on `shared.sp_api_rest.sp_api_request`,
  one exported function `fetch_buybox_status(client_id, marketplace, asins)
  -> list[dict]`.
  - Calls SP-API Pricing API v0 `getItemOffers` per ASIN
    (`GET /products/pricing/v0/items/{asin}/offers`,
    `ItemCondition=New`, `MarketplaceId=<marketplace's id>`).
  - Per ASIN, finds "our own" offer by matching `Offers[].SellerId`
    against the client's own Amazon Seller ID — already stored as
    `clients.selling_partner_id` (confirmed populated today, e.g.
    `itsbodily` → `A38K1AMGZXHFY8`) — and reads that offer's
    `IsBuyBoxWinner`. If no offer's `SellerId` matches (e.g. the account
    isn't an active offer for that ASIN — out of stock, suppressed
    listing), record `is_buybox_winner = None` rather than `False`, so
    "we don't currently have an offer" is distinguishable from "we have
    an offer and lost the box."
  - Returned row shape: `{asin, is_buybox_winner, buybox_price,
    your_price, currency, offer_count, checked_at}`. `checked_at` is a
    UTC timestamp (`datetime.now(timezone.utc).isoformat()`), since this
    is a point-in-time snapshot, not a report-date-scoped figure.
  - **Pacing**: Pricing API's sustained rate limit is low (order of ~1-2
    req/sec depending on the account's throttling tier). Calls are made
    sequentially with a fixed inter-call delay; `sp_api_rest.sp_api_request`
    already retries 429s with backoff, so this only needs to set a
    conservative baseline pace, not reimplement retry logic.
  - Exact field names (`SellerId`, `IsBuyBoxWinner`, the summary/offer
    envelope shape) are per Amazon's documented Pricing API v0 schema, but
    — following the same caveat already written into
    `replenishment_client.py` — should be confirmed against a live test
    call before relying on them, since Amazon's field casing/nesting has
    drifted from docs before in this codebase.

- **`functions/shared/api_operations.py`**: new entry
  `BUYBOX_STATUS = "BUYBOX_STATUS"`, `api_source: "sp_api"`,
  `handler: "buybox_status"`, `aggregation: None`, `bq_table: None`
  (matches the "Drive file only" decision — same shape as `SNS_OFFERS`,
  which also has `bq_table: None`).

- **`functions/fetch_api/main.py`**: new `_dispatch` branch for
  `handler_key == "buybox_status"`:
  1. Query `active_listings_latest` (the dedup view) in BigQuery for this
     `client_id` + `marketplace`, `status = 'Active'`, to get the ASIN list.
  2. Call `pricing_client.fetch_buybox_status(client_id, marketplace,
     asins)`.
  3. Return the rows — same as every other operation, `fetch_api`'s
     existing flatten-to-TSV + `upload_report` handles delivery
     unchanged.

No changes needed to `on_demand_route`, `workflow_launcher.py`, or the
`schedules` CRUD — a new `API_OPERATIONS` entry is picked up by all of
those automatically, exactly like Replenishment/Finances are today.

## Data Flow

```
on-demand trigger OR scheduled run
        │
        ▼
fetch_api (mode="api_call", report_type="BUYBOX_STATUS")
        │
        ├─▶ BigQuery: active_listings_latest WHERE client_id, marketplace, status='Active'
        │        │
        │        ▼
        │   [asin, asin, asin, ...]
        │        │
        ▼        ▼
   pricing_client.fetch_buybox_status()  ──▶  SP-API Pricing v0 getItemOffers (paced, per ASIN)
        │
        ▼
   rows: [{asin, is_buybox_winner, buybox_price, your_price, currency, offer_count, checked_at}, ...]
        │
        ▼
   rows_to_tsv() → upload_report() → Drive (existing path, unchanged)
```

`active_listings` itself is populated by the existing report pipeline
(create_report → poll → download_upload → ingest_bigquery), unchanged
except for the new registry entry — nothing new to build there beyond the
schema.

## Error Handling

- **No active-listings data yet for this client/marketplace**: the BQ
  query returns zero ASINs. Return `{"status": "completed", "rows": 0}`
  with a clear log message (`"No active listings found — has
  GET_MERCHANT_LISTINGS_ALL_DATA been pulled for this client?"`), not an
  error — this is an expected state for a client that hasn't run that
  report yet, not a failure of this operation.
- **Per-ASIN Pricing API failure** (e.g. a delisted/suppressed ASIN
  returns 404, or a transient error survives `sp_api_rest`'s retries):
  log and record that ASIN's row with `is_buybox_winner = None` and an
  `error` field, rather than aborting the whole batch — one bad ASIN
  should never blank out results for the other 99.
- **Large catalogs / timeout**: `fetch_api`'s Cloud Function timeout is
  540s. At a conservative pace this comfortably covers catalogs up to
  roughly a couple hundred ASINs; a account with a much larger catalog
  would need chunking (e.g. paginating ASINs across multiple triggered
  runs) — out of scope for this phase, called out here so it isn't a
  silent failure mode later: if `fetch_api` times out mid-run, the job is
  simply marked failed today (existing behavior), same as any other
  operation that overruns its timeout.

## Testing

- Unit tests for `pricing_client.fetch_buybox_status`: mock
  `sp_api_rest.sp_api_request` responses covering (a) our offer wins the
  box, (b) our offer exists but doesn't win, (c) we have no offer for
  that ASIN, (d) a 404/error response for one ASIN among several.
- Unit test for the new `bq_schemas` registry entry and column casts
  (mirrors the existing `test_report_converter.py`/schema test patterns).
- Manual on-demand test against the real ItsBodily account once shipped,
  the same way today's Orders/ads fixes were verified against real pulls
  before considering them done.

## Out of Scope (this phase)

- BigQuery ingestion of Buy Box results themselves (Drive TSV only, per
  the "surfacing" decision).
- Slack alerting when a Buy Box is lost — this needs a previous-status
  comparison (not just a snapshot), deferred as a clear follow-up once
  the snapshot version is proven.
- A dedicated always-on Cloud Scheduler cadence — on-demand/schedules-CRUD
  covers this for now; a fixed cadence can be added later exactly the way
  `daily-recap`'s scheduler job already works, without changing this
  design.
- Chunking/pagination for catalogs too large for a single 540s run.
