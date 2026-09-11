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

import requests
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
    if not seller_id:
        raise ValueError(f"Client {client_id} has no selling_partner_id configured")
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
    except (SPAPIRequestError, requests.RequestException) as exc:
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
