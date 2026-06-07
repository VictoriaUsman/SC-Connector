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
from datetime import datetime, timezone

import flask
from google.cloud import bigquery

from shared.bq_schemas import ColumnMapping, TableSchema, cast_value, get_table_schema
from shared.drive_client import get_service as get_drive_service
from shared.logging_setup import bind_log_context, clear_log_context, init_logging

logger = logging.getLogger(__name__)
init_logging("ingest-bigquery")

_bq_client: bigquery.Client | None = None


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

        if schema.dedup_key:
            _load_with_merge(table_ref, schema, rows, client_id, marketplace, report_date)
        else:
            _load_with_replace(table_ref, rows, client_id, marketplace, report_date)

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


def _load_with_merge(
    table_ref: str,
    schema: TableSchema,
    rows: list[dict],
    client_id: str,
    marketplace: str,
    report_date: str,
) -> None:
    """Orders dedup: MERGE on dedup_key columns — update existing, insert new."""
    client = _get_bq_client()

    suffix = f"_staging_{client_id}_{marketplace}".replace("-", "_")
    staging_table = f"{table_ref}{suffix}"

    job_config = bigquery.LoadJobConfig(
        schema=[
            bigquery.SchemaField(name=f["bq_name"], field_type=f["bq_type"])
            for f in [{"bq_name": "client_id", "bq_type": "STRING"},
                      {"bq_name": "marketplace", "bq_type": "STRING"},
                      {"bq_name": "report_date", "bq_type": "DATE"},
                      {"bq_name": "ingested_at", "bq_type": "TIMESTAMP"},
                      {"bq_name": "job_id", "bq_type": "STRING"}]
            + [{"bq_name": c.bq_name, "bq_type": c.bq_type} for c in schema.columns]
        ],
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    load_job = client.load_table_from_json(rows, staging_table, job_config=job_config)
    load_job.result()

    all_columns = (
        ["client_id", "marketplace", "report_date", "ingested_at", "job_id"]
        + [c.bq_name for c in schema.columns]
    )

    on_clause = " AND ".join(
        f"T.{k} = S.{k}" for k in schema.dedup_key
    )
    on_clause += f" AND T.client_id = S.client_id AND T.marketplace = S.marketplace"

    update_cols = ", ".join(f"T.{c} = S.{c}" for c in all_columns if c not in schema.dedup_key)
    insert_cols = ", ".join(all_columns)
    insert_vals = ", ".join(f"S.{c}" for c in all_columns)

    merge_sql = f"""
    MERGE `{table_ref}` T
    USING `{staging_table}` S
    ON {on_clause}
    WHEN MATCHED THEN
      UPDATE SET {update_cols}
    WHEN NOT MATCHED THEN
      INSERT ({insert_cols})
      VALUES ({insert_vals})
    """

    query_job = client.query(merge_sql)
    query_job.result()

    client.delete_table(staging_table, not_found_ok=True)
    logger.info("MERGE complete for %s (%d rows)", table_ref, len(rows))


def _load_with_replace(
    table_ref: str,
    rows: list[dict],
    client_id: str,
    marketplace: str,
    report_date: str,
) -> None:
    """Ads dedup: delete existing rows for the same (client, marketplace, date), then insert."""
    client = _get_bq_client()

    if report_date:
        delete_sql = f"""
        DELETE FROM `{table_ref}`
        WHERE client_id = @client_id
          AND marketplace = @marketplace
          AND report_date = @report_date
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("client_id", "STRING", client_id),
                bigquery.ScalarQueryParameter("marketplace", "STRING", marketplace),
                bigquery.ScalarQueryParameter("report_date", "DATE", report_date),
            ]
        )
        delete_job = client.query(delete_sql, job_config=job_config)
        delete_job.result()

    load_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    load_job = client.load_table_from_json(rows, table_ref, job_config=load_config)
    load_job.result()
    logger.info("Replace-load complete for %s (%d rows)", table_ref, len(rows))
