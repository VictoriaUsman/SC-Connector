---
name: amazon-sp-api-reports
description: Amazon SP API report types, Replenishment API, endpoints, rate limits, and authentication. Use when building or modifying SP API report creation, polling, downloading, synchronous API operations, Selling Partner API, Amazon seller reports, or Subscribe & Save data.
---

# Amazon SP API Reports

## Authentication (Login with Amazon)

Token endpoint:

```
POST https://api.amazon.com/auth/o2/token
Content-Type: application/x-www-form-urlencoded

grant_type=refresh_token&refresh_token={token}&client_id={id}&client_secret={secret}
```

- Returns `access_token` (valid ~3600 seconds / 1 hour)
- Required header for **all** SP API calls: `x-amz-access-token: {access_token}`
- Credentials are stored in GCP Secret Manager: `kalilos-{env}-sp-api-{client}`

### Python Implementation

```python
import requests

def get_sp_api_token(refresh_token: str, client_id: str, client_secret: str) -> str:
    response = requests.post(
        "https://api.amazon.com/auth/o2/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )
    response.raise_for_status()
    return response.json()["access_token"]
```

## Report Flow

SP API has two different data shapes in this codebase:

- **Reports API (async):** `createReport` -> poll -> report document -> download. This is the default `mode="report"` path.
- **Synchronous REST APIs:** immediate JSON responses with endpoint-specific pagination. These are registered in `functions/shared/api_operations.py` and run through `mode="api_call"` / `functions/fetch_api`.

Do not force synchronous APIs through `createReport`; add an API operation instead.

### Step 1: Create Report

```
POST https://sellingpartnerapi-na.amazon.com/reports/2021-06-30/reports
```

Request body:

```json
{
  "reportType": "GET_FLAT_FILE_OPEN_LISTINGS_DATA",
  "marketplaceIds": ["ATVPDKIKX0DER"],
  "dataStartTime": "2026-03-01T00:00:00Z",
  "dataEndTime": "2026-03-15T23:59:59Z"
}
```

Returns `reportId` (string).

### Step 2: Poll Report Status

```
GET https://sellingpartnerapi-na.amazon.com/reports/2021-06-30/reports/{reportId}
```

Returns `processingStatus` and, when DONE, `reportDocumentId`.

### Step 3: Get Report Document

```
GET https://sellingpartnerapi-na.amazon.com/reports/2021-06-30/documents/{reportDocumentId}
```

Returns `url` (pre-signed S3 download URL) and `compressionAlgorithm` (GZIP or null).

### Step 4: Download & Decompress

Download from the pre-signed URL. If `compressionAlgorithm` is `GZIP`, decompress with `gzip.decompress()`.

## Rate Limits

| Endpoint            | Rate (req/sec) | Burst |
|---------------------|----------------|-------|
| createReport        | 0.0167         | 15    |
| getReport           | 2.0            | 15    |
| getReportDocument   | 0.0167         | 15    |

`createReport` and `getReportDocument` are very slow (1 request per ~60 seconds sustained). Batch carefully and use the burst allowance.

## Replenishment API (Subscribe & Save)

Amazon removed the legacy Subscribe & Save report types `GET_FBA_SNS_PERFORMANCE_DATA` and `GET_FBA_SNS_FORECAST_DATA`. Use SP-API Replenishment v2022-11-07 instead.

Current operation ids in this project:

| Operation | Endpoint | Use |
|---|---|---|
| `SNS_OFFER_METRICS` | `POST /replenishment/2022-11-07/offers/metrics/search` | Per-ASIN/offer S&S metrics |
| `SNS_SP_METRICS` | `POST /replenishment/2022-11-07/sellingPartners/metrics/search` | Account-level S&S metrics |
| `SNS_OFFERS` | `POST /replenishment/2022-11-07/offers/search` | Offer enrollment/config |

Implementation files:

- `functions/shared/replenishment_client.py` — request shapes, week alignment, offset pagination
- `functions/shared/sp_api_rest.py` — generic LWA-authenticated SP-API REST transport
- `functions/shared/api_operations.py` — operation registry
- `functions/fetch_api/main.py` — workflow target for `mode="api_call"`

### Request Shapes

`/offers/search` uses offset pagination and nests marketplace/program filters:

```json
{
  "pagination": {"limit": 100, "offset": 0},
  "filters": {
    "marketplaceId": "ATVPDKIKX0DER",
    "programTypes": ["SUBSCRIBE_AND_SAVE"]
  }
}
```

`/offers/metrics/search` requires `aggregationFrequency`, `timeInterval`, `timePeriodType`, `marketplaceId`, and `programTypes` inside `filters`:

```json
{
  "pagination": {"limit": 100, "offset": 0},
  "metrics": ["TOTAL_SUBSCRIPTIONS_REVENUE", "SHIPPED_SUBSCRIPTION_UNITS"],
  "filters": {
    "aggregationFrequency": "WEEK",
    "timeInterval": {
      "startDate": "2026-05-24T00:00:00Z",
      "endDate": "2026-05-30T23:59:59Z"
    },
    "timePeriodType": "PERFORMANCE",
    "marketplaceId": "ATVPDKIKX0DER",
    "programTypes": ["SUBSCRIBE_AND_SAVE"]
  }
}
```

`/sellingPartners/metrics/search` uses top-level metric filters:

```json
{
  "aggregationFrequency": "WEEK",
  "timeInterval": {
    "startDate": "2026-05-24T00:00:00Z",
    "endDate": "2026-05-30T23:59:59Z"
  },
  "metrics": ["TOTAL_SUBSCRIPTIONS_REVENUE", "SHIPPED_SUBSCRIPTION_UNITS"],
  "timePeriodType": "PERFORMANCE",
  "marketplaceId": "ATVPDKIKX0DER",
  "programTypes": ["SUBSCRIBE_AND_SAVE"]
}
```

### Replenishment Gotchas

- `WEEK` aggregation is Amazon Sunday-Saturday, not arbitrary 7-day or Monday-Sunday ranges. The client aligns overlapping requested windows to Amazon weeks.
- `offers/search` and `offers/metrics/search` use `pagination.offset`, not `nextToken`.
- `shippedSubscriptionUnits` can be fractional at offer level; store it as `FLOAT`.
- Roles: Brand Analytics or Inventory and Order Tracking may be required depending on operation/account. A 403 on one Replenishment endpoint does not prove all Replenishment access is missing; test the specific endpoint.

## Status Values

| SP API Status  | Our Normalized Status |
|----------------|-----------------------|
| IN_QUEUE       | pending               |
| IN_PROGRESS    | pending               |
| DONE           | ready                 |
| CANCELLED      | failed                |
| FATAL          | failed                |

### Normalization Code

```python
SP_API_STATUS_MAP = {
    "IN_QUEUE": "pending",
    "IN_PROGRESS": "pending",
    "DONE": "ready",
    "CANCELLED": "failed",
    "FATAL": "failed",
}

def normalize_sp_status(raw_status: str) -> str:
    return SP_API_STATUS_MAP.get(raw_status, "unknown")
```

## Regional Endpoints

| Region        | Endpoint                                        |
|---------------|------------------------------------------------|
| North America | `https://sellingpartnerapi-na.amazon.com`      |
| Europe        | `https://sellingpartnerapi-eu.amazon.com`      |
| Far East      | `https://sellingpartnerapi-fe.amazon.com`      |

## Key Marketplace IDs

| Marketplace | ID              |
|-------------|-----------------|
| US          | ATVPDKIKX0DER  |
| CA          | A2EUQ1WTGCTBG2 |
| MX          | A1AM78C64UM0Y8 |
| UK          | A1F83G8C2ARO7P |
| DE          | A1PA6795UKMFR9 |
| FR          | A13V1IB3VIYZZH |
| IT          | APJ6JRA9NG5V4  |
| ES          | A1RKKUPIHCS9HS |
| JP          | A1VC38T7YXB528 |
| AU          | A39IBJ37TRP1C6 |
| IN          | A21TJRUUN4KGV  |

Marketplace determines the regional endpoint: US/CA/MX -> NA, UK/DE/FR/IT/ES -> EU, JP/AU/IN -> FE.

## Python SDK Usage

Install: `pip install python-amazon-sp-api`

```python
from sp_api.api import Reports
from sp_api.base import Marketplaces

credentials = {
    "refresh_token": "...",
    "lwa_app_id": "...",
    "lwa_client_secret": "...",
}

reports = Reports(credentials=credentials, marketplace=Marketplaces.US)

# Create report
response = reports.create_report(
    reportType="GET_FLAT_FILE_OPEN_LISTINGS_DATA",
    marketplaceIds=["ATVPDKIKX0DER"],
)
report_id = response.payload["reportId"]

# Poll status
status_response = reports.get_report(report_id)
status = status_response.payload["processingStatus"]
document_id = status_response.payload.get("reportDocumentId")

# Get download URL
if document_id:
    doc_response = reports.get_report_document(document_id)
    download_url = doc_response.payload["url"]
    compression = doc_response.payload.get("compressionAlgorithm")
```

## Output Format & Conversion

Most SP API reports return TSV (tab-separated values). However, several report types return **JSON** with nested structures that are not spreadsheet-friendly:

| Report Type | Format | Main Array Key(s) |
|---|---|---|
| `GET_SALES_AND_TRAFFIC_REPORT` | JSON | `salesAndTrafficByDate`, `salesAndTrafficByAsin` |
| `GET_BRAND_ANALYTICS_SEARCH_TERMS_REPORT` | JSON | `dataByDepartmentAndSearchTerm` |
| `GET_BRAND_ANALYTICS_MARKET_BASKET_REPORT` | JSON | `dataByDepartmentAndAsin` |
| `GET_BRAND_ANALYTICS_REPEAT_PURCHASE_REPORT` | JSON | `dataByDepartmentAndAsin` |
| `GET_BRAND_ANALYTICS_ALTERNATE_PURCHASE_REPORT` | JSON | `dataByDepartmentAndAsin` |
| `GET_LEDGER_SUMMARY_VIEW_DATA` | JSON | `ledgerSummaryViewData` |

These JSON reports are automatically flattened to TSV by `functions/shared/report_converter.py` before uploading to Google Drive. Nested objects are flattened with dot notation (e.g. `salesByDate.orderedProductSales.amount`).

**When adding a new JSON report type:**
1. Add to `_SP_API_JSON_REPORTS` in `functions/shared/drive_client.py`
2. Add the report type and its array key(s) to `_JSON_REPORT_ARRAY_KEYS` in `functions/shared/report_converter.py`

## Error Handling

- **429 Too Many Requests**: Back off and retry. SP API returns `x-amzn-RateLimit-Limit` header with current rate.
- **403 Forbidden**: Token expired or invalid. Refresh the LWA token.
- **400 Bad Request**: Invalid report type or marketplace. Check `report-types.md` for valid values.
- **500/503**: Amazon internal error. Retry with exponential backoff.

## Reference

See `report-types.md` in this directory for the full list of supported report types with descriptions and parameters.
