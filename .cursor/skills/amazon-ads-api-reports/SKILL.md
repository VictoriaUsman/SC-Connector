---
name: amazon-ads-api-reports
description: Amazon Ads API v3 reporting endpoints, report types, rate limits, and authentication. Use when building or modifying Ads API report creation, polling, or downloading logic, or when the user mentions Ads API, Sponsored Products, Sponsored Brands, or advertising reports.
---

# Amazon Ads API Reports (v3)

## Authentication

Uses the same Login with Amazon (LWA) OAuth as SP API:

```
POST https://api.amazon.com/auth/o2/token
Content-Type: application/x-www-form-urlencoded

grant_type=refresh_token&refresh_token={token}&client_id={id}&client_secret={secret}
```

Returns `access_token` valid for ~3600 seconds.

### Required Headers for All Ads API Calls

| Header | Value | Notes |
|--------|-------|-------|
| `Authorization` | `Bearer {access_token}` | LWA access token |
| `Amazon-Advertising-API-ClientId` | `{client_id}` | Your LWA app client ID |
| `Amazon-Advertising-API-Scope` | `{profile_id}` | Advertiser profile ID (one per marketplace per advertiser) |
| `Content-Type` | `application/vnd.createasyncreport.v3+json` | For report creation only |

Credentials stored in Secret Manager: `kalilos-{env}-ads-api-{client}`

## Profile Discovery

Before creating reports, discover advertiser profiles:

```
GET https://advertising-api.amazon.com/v2/profiles
```

Returns a list of profiles. Each profile contains:

```json
{
  "profileId": 1234567890,
  "countryCode": "US",
  "currencyCode": "USD",
  "accountInfo": {
    "marketplaceStringId": "ATVPDKIKX0DER",
    "id": "ENTITY123",
    "type": "seller",
    "name": "My Store"
  }
}
```

Use `profileId` as the `Amazon-Advertising-API-Scope` header value.

### Python Implementation

```python
import requests

def list_profiles(access_token: str, client_id: str) -> list[dict]:
    response = requests.get(
        "https://advertising-api.amazon.com/v2/profiles",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Amazon-Advertising-API-ClientId": client_id,
        },
    )
    response.raise_for_status()
    return response.json()
```

## Report Flow (v3 Async)

### Step 1: Create Report

```
POST https://advertising-api.amazon.com/reporting/reports
Content-Type: application/vnd.createasyncreport.v3+json
```

Request body:

```json
{
  "name": "SP Campaign Report 2026-03-01 to 2026-03-15",
  "startDate": "2026-03-01",
  "endDate": "2026-03-15",
  "configuration": {
    "adProduct": "SPONSORED_PRODUCTS",
    "groupBy": ["campaign"],
    "columns": [
      "campaignName", "campaignId", "campaignStatus",
      "impressions", "clicks", "cost", "purchases1d",
      "sales1d", "unitsSoldClicks1d"
    ],
    "reportTypeId": "spCampaigns",
    "format": "GZIP_JSON",
    "timeUnit": "DAILY"
  }
}
```

Returns:

```json
{
  "reportId": "amzn1.ads.report.abc123",
  "status": "IN_PROGRESS"
}
```

### Step 2: Poll Report Status

```
GET https://advertising-api.amazon.com/reporting/reports/{reportId}
```

Returns status and, when complete, `url`:

```json
{
  "reportId": "amzn1.ads.report.abc123",
  "status": "SUCCESS",
  "url": "https://advertising-api.amazon.com/reporting/download/..."
}
```

### Step 3: Download Report

Download from the `url` field. Response is GZIP-compressed JSON. Decompress to get an array of report rows.

```python
import gzip
import json
import requests

def download_ads_report(download_url: str, access_token: str, client_id: str, profile_id: str) -> list[dict]:
    response = requests.get(
        download_url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Amazon-Advertising-API-ClientId": client_id,
            "Amazon-Advertising-API-Scope": str(profile_id),
        },
    )
    response.raise_for_status()
    data = gzip.decompress(response.content)
    return json.loads(data)
```

## Rate Limits

| Scope | Limit |
|-------|-------|
| Per advertiser profile | ~10 requests/second |
| Report creation | ~10 concurrent pending reports per profile |
| Report download | No explicit limit (pre-signed URL) |

If you hit 429, back off exponentially starting at 1 second.

## Status Values

| Ads API Status | Our Normalized Status |
|----------------|-----------------------|
| IN_PROGRESS    | pending               |
| SUCCESS        | ready                 |
| FAILURE        | failed                |

### Normalization Code

```python
ADS_API_STATUS_MAP = {
    "IN_PROGRESS": "pending",
    "SUCCESS": "ready",
    "FAILURE": "failed",
}

def normalize_ads_status(raw_status: str) -> str:
    return ADS_API_STATUS_MAP.get(raw_status, "unknown")
```

## Regional Endpoints

| Region        | Endpoint                                          |
|---------------|--------------------------------------------------|
| North America | `https://advertising-api.amazon.com`             |
| Europe        | `https://advertising-api-eu.amazon.com`          |
| Far East      | `https://advertising-api-fe.amazon.com`          |

## Python SDK Usage

Install: `pip install python-amazon-ad-api`

```python
from ad_api.api import Reports
from ad_api.base import Marketplaces

credentials = {
    "refresh_token": "...",
    "client_id": "...",
    "client_secret": "...",
    "profile_id": "1234567890",
}

reports = Reports(credentials=credentials, marketplace=Marketplaces.US)

# Create report
response = reports.post_report(
    body={
        "name": "SP Campaign Report",
        "startDate": "2026-03-01",
        "endDate": "2026-03-15",
        "configuration": {
            "adProduct": "SPONSORED_PRODUCTS",
            "groupBy": ["campaign"],
            "columns": ["campaignName", "impressions", "clicks", "cost"],
            "reportTypeId": "spCampaigns",
            "format": "GZIP_JSON",
            "timeUnit": "DAILY",
        },
    }
)
report_id = response.payload["reportId"]

# Poll status
status_response = reports.get_report(reportId=report_id)
status = status_response.payload["status"]
download_url = status_response.payload.get("url")
```

## Common Column Sets by Report Type

### Campaign Reports (spCampaigns, sbCampaigns, sdCampaigns)

```python
CAMPAIGN_COLUMNS = [
    "campaignName", "campaignId", "campaignStatus", "campaignBudgetAmount",
    "impressions", "clicks", "cost",
    "purchases1d", "purchases7d", "purchases14d", "purchases30d",
    "sales1d", "sales7d", "sales14d", "sales30d",
]
```

### Search Term Reports (spSearchTerm, sbSearchTerm)

```python
SEARCH_TERM_COLUMNS = [
    "searchTerm", "campaignName", "adGroupName", "targeting",
    "impressions", "clicks", "cost",
    "purchases7d", "sales7d",
]
```

## Error Handling

- **400 Bad Request**: Invalid report configuration (wrong columns, dates, etc.)
- **401 Unauthorized**: Token expired. Refresh LWA token.
- **403 Forbidden**: Profile doesn't have access to this ad product.
- **429 Too Many Requests**: Rate limited. Back off and retry.
- **FAILURE status**: Report generation failed on Amazon's side. Retry the creation.

## Reference

See `ads-report-types.md` in this directory for the full list of report type IDs and their supported columns.
