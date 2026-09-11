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
