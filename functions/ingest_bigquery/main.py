"""Ingest a report file from Google Drive into BigQuery.

Called by the workflow after download_upload. Downloads the report via its
Drive file ID, parses TSV rows, casts to the schema, and loads into the
appropriate BigQuery table.

Report types not registered in bq_schemas are gracefully skipped (200).
"""

from __future__ import annotations

import csv
import io
import logging
import os
import random
import time
from datetime import datetime, timezone
from typing import Callable, TypeVar

import flask
from google.cloud import bigquery

from shared.bq_schemas import ColumnMapping, TableSchema, cast_value, get_table_schema
from shared.drive_client import get_service as get_drive_service
from shared.logging_setup import bind_log_context, clear_log_context, init_logging

logger = logging.getLogger(__name__)
init_logging("ingest-bigquery")

_bq_client: bigquery.Client | None = None

_T = TypeVar("_T")

# Substrings that mark a transient BigQuery failure worth retrying. Append-only
# load jobs take no table DML lock, so "concurrent update" should no longer
# occur — but we retry defensively against rate limiting / backend blips.
_TRANSIENT_ERROR_MARKERS = (
    "could not serialize access",
    "concurrent update",
    "ratelimitexceeded",
    "rate limit exceeded",
    "backenderror",
    "internalerror",
    "service unavailable",
    "try again later",
)


def _run_with_retry(
    fn: Callable[[], _T],
    *,
    attempts: int = 5,
    base_delay: float = 2.0,
) -> _T:
    """Run ``fn``, retrying transient BigQuery errors with exponential backoff."""
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — re-raised below if non-transient
            message = str(exc).lower()
            if not any(marker in message for marker in _TRANSIENT_ERROR_MARKERS):
                raise
            last_exc = exc
            if attempt == attempts - 1:
                break
            delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
            logger.warning(
                "Transient BigQuery error, retrying",
                extra={"attempt": attempt + 1, "delay": round(delay, 2)},
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def _get_bq_client() -> bigquery.Client:
    global _bq_client
    if _bq_client is None:
        project = os.environ.get("GCP_PROJECT", "")
        _bq_client = bigquery.Client(project=project) if project else bigquery.Client()
    return _bq_client


def handler(request: flask.Request) -> tuple[dict, int]:
    data = request.get_json(silent=True) or {}

    report_type = data.get("report_type", "")
    api_source = data.get("api_source", "")
    client_id = data.get("client_id", "")
    marketplace = data.get("marketplace", "")
    report_date = data.get("report_date", "")
    job_id = data.get("job_id", "")
    gdrive_file_id = data.get("gdrive_file_id", "")

    if not all([report_type, api_source, client_id, marketplace, gdrive_file_id]):
        return {"error": "Missing required fields", "code": "INVALID_REQUEST"}, 400

    clear_log_context()
    bind_log_context(
        job_id=job_id,
        client_id=client_id,
        api_source=api_source,
        marketplace=marketplace,
        report_type=report_type,
        phase="ingest_bigquery",
    )

    schema = get_table_schema(report_type, api_source)
    if schema is None:
        logger.info(
            "Report type not registered for BQ ingestion, skipping",
            extra={"report_type": report_type, "api_source": api_source},
        )
        return {"status": "skipped", "reason": "unregistered_report_type"}, 200

    try:
        content = _download_from_drive(gdrive_file_id)
        rows = _parse_tsv(content, schema, client_id, marketplace, report_date, job_id)

        if not rows:
            logger.warning(
                "No rows parsed from report",
                extra={"report_type": report_type, "gdrive_file_id": gdrive_file_id},
            )
            return {"status": "completed", "rows_loaded": 0}, 200

        dataset = os.environ.get("BQ_DATASET", "")
        if not dataset:
            project = os.environ.get("GCP_PROJECT", "")
            env = os.environ.get("ENVIRONMENT", "staging")
            dataset = f"kalilos_reports_{env}"

        table_ref = f"{_get_bq_client().project}.{dataset}.{schema.table_name}"

        _load_append(table_ref, schema, rows)

        logger.info(
            "BQ ingestion complete",
            extra={
                "table": schema.table_name,
                "rows": len(rows),
                "client_id": client_id,
                "marketplace": marketplace,
            },
        )
        return {"status": "completed", "rows_loaded": len(rows), "table": schema.table_name}, 200

    except Exception as exc:
        logger.exception(
            "BQ ingestion failed",
            extra={"report_type": report_type, "client_id": client_id},
        )
        return {"error": str(exc), "code": "INGESTION_FAILED"}, 500


def _download_from_drive(file_id: str) -> bytes:
    """Download file content from Google Drive by file ID."""
    service = get_drive_service()
    # Export Google Sheets as TSV; download regular files directly
    meta = service.files().get(
        fileId=file_id, fields="mimeType", supportsAllDrives=True,
    ).execute()

    if meta.get("mimeType") == "application/vnd.google-apps.spreadsheet":
        return service.files().export(
            fileId=file_id, mimeType="text/tab-separated-values",
        ).execute()

    return service.files().get_media(
        fileId=file_id, supportsAllDrives=True,
    ).execute()


def _parse_tsv(
    content: bytes,
    schema: TableSchema,
    client_id: str,
    marketplace: str,
    report_date: str,
    job_id: str,
) -> list[dict]:
    """Parse TSV content into a list of BQ-ready row dicts."""
    text = content.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")

    if not reader.fieldnames:
        return []

    header_to_col: dict[str, ColumnMapping] = {
        col.tsv_header: col for col in schema.columns
    }

    now = datetime.now(timezone.utc).isoformat()
    rows: list[dict] = []

    for raw_row in reader:
        row: dict = {
            "client_id": client_id,
            "marketplace": marketplace,
            "report_date": report_date or datetime.now(timezone.utc).date().isoformat(),
            "ingested_at": now,
            "job_id": job_id or None,
        }

        for tsv_header, col in header_to_col.items():
            raw_val = raw_row.get(tsv_header, "")
            row[col.bq_name] = cast_value(raw_val, col.bq_type)

        rows.append(row)

    return rows


def _dedupe_merge_rows(rows: list[dict], schema: TableSchema) -> list[dict]:
    """Collapse rows that share the same MERGE key down to one row each.

    BigQuery's MERGE rejects a target row that matches more than one source
    row ("UPDATE/MERGE must match at most one source row for each target row").
    Amazon's by-last-update All Orders flat file re-emits the same
    ``(amazon_order_id, sku)`` line — across pulls and occasionally within a
    single pull — so the staging table could hold duplicate dedup keys and the
    MERGE failed on every run after the first, freezing the ingested data.

    Keep the most recently updated row per key (matching the WHEN MATCHED
    UPDATE "latest wins" semantics) so the staging source is unique and the
    MERGE always succeeds.
    """
    if not schema.dedup_key:
        return rows

    key_cols = (*schema.dedup_key, "client_id", "marketplace")

    def recency(row: dict) -> str:
        # ISO-8601 strings sort chronologically; fall back to ingest time.
        return str(row.get("last_updated_date") or row.get("ingested_at") or "")

    best: dict[tuple, dict] = {}
    for row in rows:
        key = tuple(row.get(col) for col in key_cols)
        existing = best.get(key)
        if existing is None or recency(row) >= recency(existing):
            best[key] = row
    return list(best.values())


def _load_append(table_ref: str, schema: TableSchema, rows: list[dict]) -> None:
    """Append a pull's rows to the target table (no DML, no table lock).

    Ingestion is append-only: every pull writes a fresh batch tagged with the
    same ``ingested_at`` timestamp. Append load jobs take no table-level DML
    lock, so concurrent ingestions from many workflows never collide — this
    replaced the MERGE/DELETE strategy that raised "Could not serialize access
    ... due to concurrent update" during high-fan-out events.

    For schemas with a ``dedup_key`` (orders), the batch is still collapsed to
    one row per key (latest update wins) so a single pull never re-counts a
    re-emitted ``(amazon_order_id, sku)`` line. Cross-pull de-duplication then
    happens at read time via the ``*_latest`` views, which keep only the most
    recent pull per (client, marketplace, report_date).
    """
    client = _get_bq_client()

    if schema.dedup_key:
        rows = _dedupe_merge_rows(rows, schema)

    load_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )

    def _do_load() -> None:
        client.load_table_from_json(rows, table_ref, job_config=load_config).result()

    _run_with_retry(_do_load)
    logger.info("Append-load complete for %s (%d rows)", table_ref, len(rows))
