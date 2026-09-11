# Buy Box Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Kalilos check, per ASIN, whether the client currently holds the Amazon Buy Box, delivered as an on-demand/schedulable report.

**Architecture:** Register the existing (already-pulled, never-ingested) `GET_MERCHANT_LISTINGS_ALL_DATA` report for BigQuery ingestion as the active-ASIN source; add a new `pricing_client.py` that queries that table then calls SP-API's Pricing API v0 `getItemOffers` per ASIN; wire it in as a standard synchronous API operation (`shared.api_operations` + `fetch_api`'s dispatch) so on-demand and scheduling both work without further plumbing.

**Tech Stack:** Python 3.12, `shared.sp_api_rest.sp_api_request` (SP-API REST transport), `google-cloud-bigquery`, pytest + `unittest.mock`.

**Spec:** `docs/superpowers/specs/2026-09-11-buybox-status-design.md`

## Global Constraints

- Pricing API v0 `getItemOffers`: `GET /products/pricing/v0/items/{asin}/offers`, required query params `MarketplaceId` and `ItemCondition=New`.
- "Our own offer" = the entry in the response's `Offers[]` whose `SellerId` equals `clients.selling_partner_id` for that client (confirmed populated, e.g. `itsbodily` → `A38K1AMGZXHFY8`).
- `bq_table: None` for the new operation — Drive TSV delivery only, no BigQuery ingestion of Buy Box results (per spec).
- A single ASIN's failure must never abort the whole batch — record that row with `is_buybox_winner: None` and an `error` field, continue.
- Pace Pricing API calls conservatively (fixed inter-call delay) — `sp_api_rest.sp_api_request` already retries 429/5xx with backoff; this only needs a sane baseline, not new retry logic.

---

### Task 1: Register Active Listings for BigQuery ingestion

**Files:**
- Modify: `functions/shared/bq_schemas.py`
- Modify: `infra/resources/bigquery.py`
- Test: `tests/test_bq_schemas.py` (new file)

**Interfaces:**
- Produces: `bq_schemas.get_table_schema("GET_MERCHANT_LISTINGS_ALL_DATA", "sp_api")` returns a `TableSchema` with `table_name="active_listings"`, columns `sku`/`asin`/`item_name`/`status`/`fulfillment_channel`/`price`/`quantity`, `dedup_key=("sku",)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_bq_schemas.py`:

```python
"""Tests for shared.bq_schemas — the Active Listings registry entry."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))


class TestActiveListingsSchema:
    def test_registered_for_ingestion(self):
        from shared.bq_schemas import get_table_schema

        schema = get_table_schema("GET_MERCHANT_LISTINGS_ALL_DATA", "sp_api")
        assert schema is not None
        assert schema.table_name == "active_listings"
        assert schema.dedup_key == ("sku",)

    def test_columns_map_expected_tsv_headers(self):
        from shared.bq_schemas import get_table_schema

        schema = get_table_schema("GET_MERCHANT_LISTINGS_ALL_DATA", "sp_api")
        by_name = {c.bq_name: c for c in schema.columns}
        assert by_name["sku"].tsv_header == "seller-sku"
        assert by_name["asin"].tsv_header == "asin1"
        assert by_name["status"].tsv_header == "status"
        assert by_name["price"].bq_type == "FLOAT"
        assert by_name["quantity"].bq_type == "INTEGER"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bq_schemas.py -v`
Expected: FAIL — `get_table_schema` returns `None` (`assert schema is not None` fails).

- [ ] **Step 3: Add the schema to `functions/shared/bq_schemas.py`**

Add near the other `TableSchema` definitions (e.g. right after `_ORDERS_SCHEMA`):

```python
# ---------------------------------------------------------------------------
# Active Listings — GET_MERCHANT_LISTINGS_ALL_DATA
# ---------------------------------------------------------------------------
_ACTIVE_LISTINGS_COLUMNS = (
    ColumnMapping("sku", "STRING", "seller-sku"),
    ColumnMapping("asin", "STRING", "asin1"),
    ColumnMapping("item_name", "STRING", "item-name"),
    ColumnMapping("status", "STRING", "status"),
    ColumnMapping("fulfillment_channel", "STRING", "fulfillment-channel"),
    ColumnMapping("price", "FLOAT", "price"),
    ColumnMapping("quantity", "INTEGER", "quantity"),
)

_ACTIVE_LISTINGS_SCHEMA = TableSchema(
    table_name="active_listings",
    columns=_ACTIVE_LISTINGS_COLUMNS,
    dedup_key=("sku",),
)
```

Then add to `_REGISTRY`:

```python
    ("GET_MERCHANT_LISTINGS_ALL_DATA", "sp_api"): _ACTIVE_LISTINGS_SCHEMA,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_bq_schemas.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Add the BigQuery table definition in `infra/resources/bigquery.py`**

Add near `_ORDERS_COLUMNS`:

```python
_ACTIVE_LISTINGS_COLUMNS = [
    {"name": "sku", "type": "STRING", "mode": "REQUIRED"},
    {"name": "asin", "type": "STRING", "mode": "NULLABLE"},
    {"name": "item_name", "type": "STRING", "mode": "NULLABLE"},
    {"name": "status", "type": "STRING", "mode": "NULLABLE"},
    {"name": "fulfillment_channel", "type": "STRING", "mode": "NULLABLE"},
    {"name": "price", "type": "FLOAT", "mode": "NULLABLE"},
    {"name": "quantity", "type": "INTEGER", "mode": "NULLABLE"},
]
```

Add to `TABLE_DEFS` (list of dicts), matching the `orders` entry's shape:

```python
    {
        "name": "active_listings",
        "columns": _ACTIVE_LISTINGS_COLUMNS,
        "partition_field": "report_date",
        "clustering": ["client_id", "marketplace"],
        "description": "Active Listings — current catalog (asin/sku/status), used as the Buy Box check's ASIN source",
        "latest_keys": ("sku",),
    },
```

- [ ] **Step 6: Commit**

```bash
git add functions/shared/bq_schemas.py infra/resources/bigquery.py tests/test_bq_schemas.py
git commit -m "Register GET_MERCHANT_LISTINGS_ALL_DATA for BQ ingestion (active_listings)"
```

---

### Task 2: `pricing_client.get_active_asins()` — read the ASIN source from BigQuery

**Files:**
- Create: `functions/shared/pricing_client.py`
- Test: `tests/test_pricing_client.py` (new file)

**Interfaces:**
- Consumes: nothing from other tasks (first piece of the new module).
- Produces: `pricing_client.get_active_asins(client_id: str, marketplace: str) -> list[str]` — used by Task 4's `fetch_api` dispatch branch.

- [ ] **Step 1: Write the failing test**

Create `tests/test_pricing_client.py`:

```python
"""Tests for shared.pricing_client — active-ASIN sourcing + Buy Box checks."""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))

os.environ.setdefault("GCP_PROJECT", "test-project")
os.environ.setdefault("ENVIRONMENT", "staging")
os.environ.setdefault("BQ_DATASET", "test_dataset")


class TestGetActiveAsins:
    def test_queries_active_listings_latest_and_returns_asins(self):
        from shared import pricing_client

        fake_rows = [{"asin": "B0ABC12345"}, {"asin": "B0DEF67890"}]
        fake_client = MagicMock()
        fake_client.query.return_value = fake_rows

        with patch.object(pricing_client, "_get_bq_client", return_value=fake_client):
            asins = pricing_client.get_active_asins("itsbodily", "US")

        assert asins == ["B0ABC12345", "B0DEF67890"]
        query_sql = fake_client.query.call_args[0][0]
        assert "active_listings_latest" in query_sql
        assert "status" in query_sql

    def test_empty_result_returns_empty_list(self):
        from shared import pricing_client

        fake_client = MagicMock()
        fake_client.query.return_value = []

        with patch.object(pricing_client, "_get_bq_client", return_value=fake_client):
            asins = pricing_client.get_active_asins("itsbodily", "US")

        assert asins == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pricing_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shared.pricing_client'`

- [ ] **Step 3: Write minimal implementation**

Create `functions/shared/pricing_client.py`:

```python
"""SP-API Pricing (v0) client — live Buy Box status per ASIN.

The Pricing API has no bulk "list my ASINs" endpoint, so the ASIN list to
check comes from the Active Listings report (GET_MERCHANT_LISTINGS_ALL_DATA),
already registered for BigQuery ingestion (see shared.bq_schemas). This
module reads that table, then calls getItemOffers per ASIN.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from google.cloud import bigquery

from shared.config import get_marketplace_id
from shared.db import get_client
from shared.sp_api_rest import SPAPIRequestError, sp_api_request

logger = logging.getLogger(__name__)

_PACE_SECONDS = 0.6  # conservative baseline; sp_api_request already retries 429s.

_bq_client: bigquery.Client | None = None


def _get_bq_client() -> bigquery.Client:
    global _bq_client
    if _bq_client is None:
        project = os.environ.get("GCP_PROJECT", "")
        _bq_client = bigquery.Client(project=project) if project else bigquery.Client()
    return _bq_client


def get_active_asins(client_id: str, marketplace: str) -> list[str]:
    """Return the client's currently-active ASINs for *marketplace*.

    Reads the deduplicated Active Listings view. Empty (not an error) when
    the client has never had GET_MERCHANT_LISTINGS_ALL_DATA pulled/ingested.
    """
    dataset = os.environ.get("BQ_DATASET", "")
    project = os.environ.get("GCP_PROJECT", "")
    client = _get_bq_client()

    query = f"""
        SELECT DISTINCT asin
        FROM `{project}.{dataset}.active_listings_latest`
        WHERE client_id = @client_id
          AND marketplace = @marketplace
          AND status = 'Active'
          AND asin IS NOT NULL
          AND asin != ''
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("client_id", "STRING", client_id),
            bigquery.ScalarQueryParameter("marketplace", "STRING", marketplace),
        ]
    )
    rows = client.query(query, job_config=job_config)
    return [row["asin"] for row in rows]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_pricing_client.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add functions/shared/pricing_client.py tests/test_pricing_client.py
git commit -m "Add pricing_client.get_active_asins (BQ-sourced ASIN list)"
```

---

### Task 3: `pricing_client.fetch_buybox_status()` — the actual Buy Box check

**Files:**
- Modify: `functions/shared/pricing_client.py`
- Test: `tests/test_pricing_client.py`

**Interfaces:**
- Consumes: `sp_api_request(client_id, marketplace, method, path, *, params=None, body=None, timeout=...) -> dict` (raises `SPAPIRequestError(status_code, message, body=...)` on non-2xx/non-retryable); `get_client(client_id) -> dict | None` (has `selling_partner_id` key); `get_marketplace_id(marketplace) -> str`.
- Produces: `pricing_client.fetch_buybox_status(client_id: str, marketplace: str, asins: list[str]) -> list[dict]` — used by Task 4's `fetch_api` dispatch branch. Each row: `{"asin": str, "is_buybox_winner": bool | None, "buybox_price": float | None, "your_price": float | None, "currency": str | None, "offer_count": int, "checked_at": str, "error": str | None}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pricing_client.py`:

```python
class TestFetchBuyboxStatus:
    def _client_row(self):
        return {"id": "itsbodily", "selling_partner_id": "A38K1AMGZXHFY8"}

    def test_our_offer_wins_the_box(self):
        from shared import pricing_client

        response = {
            "payload": {
                "Summary": {"BuyBoxPrices": [{"ListingPrice": {"Amount": 19.99, "CurrencyCode": "USD"}}]},
                "Offers": [
                    {"SellerId": "A38K1AMGZXHFY8", "IsBuyBoxWinner": True,
                     "ListingPrice": {"Amount": 19.99, "CurrencyCode": "USD"}},
                    {"SellerId": "OTHERSELLER", "IsBuyBoxWinner": False,
                     "ListingPrice": {"Amount": 21.50, "CurrencyCode": "USD"}},
                ],
            }
        }
        with patch.object(pricing_client, "get_client", return_value=self._client_row()), \
             patch.object(pricing_client, "sp_api_request", return_value=response) as mock_req:
            rows = pricing_client.fetch_buybox_status("itsbodily", "US", ["B0ABC12345"])

        assert rows == [{
            "asin": "B0ABC12345",
            "is_buybox_winner": True,
            "buybox_price": 19.99,
            "your_price": 19.99,
            "currency": "USD",
            "offer_count": 2,
            "checked_at": rows[0]["checked_at"],
            "error": None,
        }]
        path = mock_req.call_args[0][3]
        assert path == "/products/pricing/v0/items/B0ABC12345/offers"

    def test_our_offer_exists_but_does_not_win(self):
        from shared import pricing_client

        response = {
            "payload": {
                "Summary": {"BuyBoxPrices": [{"ListingPrice": {"Amount": 21.50, "CurrencyCode": "USD"}}]},
                "Offers": [
                    {"SellerId": "A38K1AMGZXHFY8", "IsBuyBoxWinner": False,
                     "ListingPrice": {"Amount": 22.00, "CurrencyCode": "USD"}},
                    {"SellerId": "OTHERSELLER", "IsBuyBoxWinner": True,
                     "ListingPrice": {"Amount": 21.50, "CurrencyCode": "USD"}},
                ],
            }
        }
        with patch.object(pricing_client, "get_client", return_value=self._client_row()), \
             patch.object(pricing_client, "sp_api_request", return_value=response):
            rows = pricing_client.fetch_buybox_status("itsbodily", "US", ["B0ABC12345"])

        assert rows[0]["is_buybox_winner"] is False
        assert rows[0]["your_price"] == 22.00
        assert rows[0]["buybox_price"] == 21.50

    def test_no_offer_for_this_seller(self):
        from shared import pricing_client

        response = {
            "payload": {
                "Summary": {"BuyBoxPrices": [{"ListingPrice": {"Amount": 21.50, "CurrencyCode": "USD"}}]},
                "Offers": [
                    {"SellerId": "OTHERSELLER", "IsBuyBoxWinner": True,
                     "ListingPrice": {"Amount": 21.50, "CurrencyCode": "USD"}},
                ],
            }
        }
        with patch.object(pricing_client, "get_client", return_value=self._client_row()), \
             patch.object(pricing_client, "sp_api_request", return_value=response):
            rows = pricing_client.fetch_buybox_status("itsbodily", "US", ["B0ABC12345"])

        assert rows[0]["is_buybox_winner"] is None
        assert rows[0]["your_price"] is None
        assert rows[0]["error"] is None

    def test_one_asin_failure_does_not_abort_the_batch(self):
        from shared import pricing_client
        from shared.sp_api_rest import SPAPIRequestError

        ok_response = {
            "payload": {
                "Summary": {"BuyBoxPrices": []},
                "Offers": [{"SellerId": "A38K1AMGZXHFY8", "IsBuyBoxWinner": True,
                            "ListingPrice": {"Amount": 10.0, "CurrencyCode": "USD"}}],
            }
        }

        def _side_effect(client_id, marketplace, method, path, **kwargs):
            if "BADASIN" in path:
                raise SPAPIRequestError(404, "Not Found")
            return ok_response

        with patch.object(pricing_client, "get_client", return_value=self._client_row()), \
             patch.object(pricing_client, "sp_api_request", side_effect=_side_effect), \
             patch.object(pricing_client.time, "sleep"):
            rows = pricing_client.fetch_buybox_status(
                "itsbodily", "US", ["BADASIN", "GOODASIN"],
            )

        assert len(rows) == 2
        assert rows[0]["asin"] == "BADASIN"
        assert rows[0]["is_buybox_winner"] is None
        assert rows[0]["error"] == "SP-API 404: Not Found"
        assert rows[1]["asin"] == "GOODASIN"
        assert rows[1]["is_buybox_winner"] is True

    def test_unknown_client_raises(self):
        from shared import pricing_client

        with patch.object(pricing_client, "get_client", return_value=None):
            try:
                pricing_client.fetch_buybox_status("nosuchclient", "US", ["B0ABC12345"])
                assert False, "expected ValueError"
            except ValueError as exc:
                assert "nosuchclient" in str(exc)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pricing_client.py -v`
Expected: FAIL — `AttributeError: module 'shared.pricing_client' has no attribute 'fetch_buybox_status'` (and `get_client`/`sp_api_rest` not yet imported into the module for patching).

- [ ] **Step 3: Implement `fetch_buybox_status`**

Add to `functions/shared/pricing_client.py`:

```python
def fetch_buybox_status(
    client_id: str,
    marketplace: str,
    asins: list[str],
) -> list[dict[str, Any]]:
    """Check live Buy Box status for each ASIN in *asins*.

    One row per ASIN, in the same order given. A single ASIN's failure
    (delisted, transient error surviving sp_api_request's own retries) is
    recorded on that row rather than raised, so the rest of the batch still
    completes.
    """
    client = get_client(client_id)
    if not client:
        raise ValueError(f"Unknown client_id: {client_id}")
    seller_id = client.get("selling_partner_id")
    marketplace_id = get_marketplace_id(marketplace)

    rows: list[dict[str, Any]] = []
    for i, asin in enumerate(asins):
        if i > 0:
            time.sleep(_PACE_SECONDS)
        rows.append(_check_one_asin(client_id, marketplace, marketplace_id, seller_id, asin))
    return rows


def _check_one_asin(
    client_id: str,
    marketplace: str,
    marketplace_id: str,
    seller_id: str | None,
    asin: str,
) -> dict[str, Any]:
    checked_at = datetime.now(timezone.utc).isoformat()
    try:
        resp = sp_api_request(
            client_id, marketplace, "GET",
            f"/products/pricing/v0/items/{asin}/offers",
            params={"MarketplaceId": marketplace_id, "ItemCondition": "New"},
        )
    except SPAPIRequestError as exc:
        logger.warning(
            "Buy Box check failed for one ASIN — recording and continuing",
            extra={"client_id": client_id, "asin": asin, "error": str(exc)},
        )
        return {
            "asin": asin, "is_buybox_winner": None, "buybox_price": None,
            "your_price": None, "currency": None, "offer_count": 0,
            "checked_at": checked_at, "error": str(exc),
        }

    payload = resp.get("payload", {})
    offers = payload.get("Offers", [])
    our_offer = next((o for o in offers if o.get("SellerId") == seller_id), None)

    buybox_price = None
    buybox_prices = payload.get("Summary", {}).get("BuyBoxPrices", [])
    if buybox_prices:
        buybox_price = buybox_prices[0].get("ListingPrice", {}).get("Amount")

    return {
        "asin": asin,
        "is_buybox_winner": our_offer.get("IsBuyBoxWinner") if our_offer else None,
        "buybox_price": buybox_price,
        "your_price": our_offer.get("ListingPrice", {}).get("Amount") if our_offer else None,
        "currency": our_offer.get("ListingPrice", {}).get("CurrencyCode") if our_offer else None,
        "offer_count": len(offers),
        "checked_at": checked_at,
        "error": None,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pricing_client.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add functions/shared/pricing_client.py tests/test_pricing_client.py
git commit -m "Add pricing_client.fetch_buybox_status (SP-API Pricing v0 getItemOffers)"
```

---

### Task 4: Wire in as a standard API operation

**Files:**
- Modify: `functions/shared/api_operations.py`
- Modify: `functions/fetch_api/main.py`
- Modify: `infra/resources/functions.py`
- Test: `tests/test_fetch_api.py`

**Interfaces:**
- Consumes: `pricing_client.get_active_asins(client_id, marketplace) -> list[str]` (Task 2), `pricing_client.fetch_buybox_status(client_id, marketplace, asins) -> list[dict]` (Task 3).
- Produces: `BUYBOX_STATUS = "BUYBOX_STATUS"` operation, dispatchable via the existing `/on-demand` route and `schedules` table — no other file needs to change for that to work.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_fetch_api.py`:

```python
class TestDispatchBuyboxStatus:
    def test_routes_to_pricing_client(self):
        from fetch_api import main as fa

        op = {"handler": "buybox_status"}
        with patch.object(fa.pricing_client, "get_active_asins", return_value=["B0ABC12345"]) as mock_asins, \
             patch.object(fa.pricing_client, "fetch_buybox_status", return_value=[{"asin": "B0ABC12345"}]) as mock_check:
            rows = fa._dispatch(op, "itsbodily", "US", date(2026, 9, 11), date(2026, 9, 11), {})

        mock_asins.assert_called_once_with("itsbodily", "US")
        mock_check.assert_called_once_with("itsbodily", "US", ["B0ABC12345"])
        assert rows == [{"asin": "B0ABC12345"}]

    def test_no_active_asins_returns_empty_without_calling_pricing_api(self):
        from fetch_api import main as fa

        op = {"handler": "buybox_status"}
        with patch.object(fa.pricing_client, "get_active_asins", return_value=[]), \
             patch.object(fa.pricing_client, "fetch_buybox_status") as mock_check:
            rows = fa._dispatch(op, "itsbodily", "US", date(2026, 9, 11), date(2026, 9, 11), {})

        mock_check.assert_not_called()
        assert rows == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_fetch_api.py -v`
Expected: FAIL — `AttributeError: module 'fetch_api.main' has no attribute 'pricing_client'`

- [ ] **Step 3: Add the operation to `functions/shared/api_operations.py`**

Add near the other constants:

```python
BUYBOX_STATUS = "BUYBOX_STATUS"
```

Add to `API_OPERATIONS`:

```python
    BUYBOX_STATUS: {
        "api_source": "sp_api",
        "label": "Buy Box Status",
        "description": (
            "Live, point-in-time Buy Box winner status per active ASIN, "
            "from the Pricing API (v0 getItemOffers). ASINs come from the "
            "client's Active Listings (GET_MERCHANT_LISTINGS_ALL_DATA)."
        ),
        "handler": "buybox_status",
        "aggregation": None,
        "bq_table": None,
    },
```

- [ ] **Step 4: Wire the dispatch in `functions/fetch_api/main.py`**

Change the existing import line:

```python
from shared import finances_client, replenishment_client
```

to:

```python
from shared import finances_client, pricing_client, replenishment_client
```

Add a branch in `_dispatch`, before the final `raise ValueError(...)`:

```python
    if handler_key == "buybox_status":
        asins = pricing_client.get_active_asins(client_id, marketplace)
        if not asins:
            return []
        return pricing_client.fetch_buybox_status(client_id, marketplace, asins)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_fetch_api.py -v`
Expected: PASS (all tests)

- [ ] **Step 6: Add `BQ_DATASET` to fetch-api's deployed env vars in `infra/resources/functions.py`**

`pricing_client.get_active_asins` reads `BQ_DATASET` — currently only `ingest-bigquery`, `slack-bot`, and `daily-recap` get it. Change:

```python
        if fn_name == "ingest-bigquery":
            env_vars["BQ_DATASET"] = f"kalilos_reports_{env}"
```

to:

```python
        if fn_name in ("ingest-bigquery", "fetch-api"):
            env_vars["BQ_DATASET"] = f"kalilos_reports_{env}"
```

- [ ] **Step 7: Run the full test suite**

Run: `pytest tests/ -v`
Expected: PASS (no regressions in any other test file)

- [ ] **Step 8: Commit**

```bash
git add functions/shared/api_operations.py functions/fetch_api/main.py infra/resources/functions.py tests/test_fetch_api.py
git commit -m "Wire Buy Box status in as a standard API operation (BUYBOX_STATUS)"
```

---

## After This Plan (deployment, not part of the plan's tasks)

Once all 4 tasks are committed, this needs the same manual-deploy treatment
today's other fixes got (Pulumi's state issue is still unresolved — see
memory): redeploy `kalilos-dev-ingest-bigquery` and `kalilos-dev-fetch-api`
via `gcloud functions deploy` with the current source, add the
`BQ_DATASET` env var to `fetch-api`'s live config, and add the
`active_listings` table's schema to the live BigQuery dataset (same
"describe → append SchemaField → update_table" approach used for
`purchase_date_local`) — since Pulumi can't apply it directly right now.
Then pull `GET_MERCHANT_LISTINGS_ALL_DATA` at least once for a test client
so `active_listings_latest` has rows, and run `BUYBOX_STATUS` on-demand
against that same client to verify against real data, the same way
today's other fixes were verified before considering them done.
