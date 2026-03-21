# Ads API Report Types Reference

## Sponsored Products

### spCampaigns

Campaign-level performance metrics for Sponsored Products.

**groupBy options:** `campaign`

**Key columns:**
- Dimensions: `campaignName`, `campaignId`, `campaignStatus`, `campaignBudgetAmount`, `campaignBudgetType`
- Metrics: `impressions`, `clicks`, `cost`, `purchases1d`, `purchases7d`, `purchases14d`, `purchases30d`, `sales1d`, `sales7d`, `sales14d`, `sales30d`, `unitsSoldClicks1d`, `unitsSoldClicks7d`, `unitsSoldClicks14d`, `unitsSoldClicks30d`
- Time: `date`

### spSearchTerm

Search term performance for Sponsored Products.

**groupBy options:** `searchTerm`

**Key columns:**
- Dimensions: `searchTerm`, `campaignName`, `campaignId`, `adGroupName`, `adGroupId`, `targeting`, `keywordId`, `keywordType`
- Metrics: `impressions`, `clicks`, `cost`, `purchases7d`, `sales7d`, `unitsSoldClicks7d`
- Time: `date`

### spTargeting

Targeting-level performance for Sponsored Products.

**groupBy options:** `targeting`

**Key columns:**
- Dimensions: `targeting`, `targetingId`, `targetingType`, `campaignName`, `campaignId`, `adGroupName`, `adGroupId`
- Metrics: `impressions`, `clicks`, `cost`, `purchases7d`, `sales7d`, `unitsSoldClicks7d`
- Time: `date`

### spAdvertisedProduct

ASIN-level performance for Sponsored Products.

**groupBy options:** `advertiser`

**Key columns:**
- Dimensions: `advertisedAsin`, `advertisedSku`, `campaignName`, `campaignId`, `adGroupName`, `adGroupId`
- Metrics: `impressions`, `clicks`, `cost`, `purchases7d`, `sales7d`, `unitsSoldClicks7d`
- Time: `date`

---

## Sponsored Brands

### sbCampaigns

Campaign-level performance for Sponsored Brands.

**groupBy options:** `campaign`

**Key columns:**
- Dimensions: `campaignName`, `campaignId`, `campaignStatus`, `campaignBudgetAmount`
- Metrics: `impressions`, `clicks`, `cost`, `purchases14d`, `sales14d`, `unitsSoldClicks14d`, `detailPageViews14d`, `newToBrandPurchases14d`, `newToBrandSales14d`
- Time: `date`

### sbSearchTerm

Search term performance for Sponsored Brands.

**groupBy options:** `searchTerm`

**Key columns:**
- Dimensions: `searchTerm`, `campaignName`, `campaignId`, `adGroupName`, `adGroupId`
- Metrics: `impressions`, `clicks`, `cost`, `purchases14d`, `sales14d`
- Time: `date`

---

## Sponsored Display

### sdCampaigns

Campaign-level performance for Sponsored Display.

**groupBy options:** `campaign`

**Key columns:**
- Dimensions: `campaignName`, `campaignId`, `campaignStatus`, `campaignBudgetAmount`
- Metrics: `impressions`, `clicks`, `cost`, `purchases14d`, `sales14d`, `viewImpressions`, `viewAttributedSales14d`, `viewAttributedPurchases14d`
- Time: `date`

### sdTargeting

Targeting-level performance for Sponsored Display.

**groupBy options:** `targeting`

**Key columns:**
- Dimensions: `targeting`, `targetingId`, `campaignName`, `campaignId`, `adGroupName`, `adGroupId`
- Metrics: `impressions`, `clicks`, `cost`, `purchases14d`, `sales14d`, `viewImpressions`
- Time: `date`

---

## Common Configuration Options

### timeUnit

- `DAILY` — one row per day per dimension combination
- `SUMMARY` — aggregated over the entire date range

### format

- `GZIP_JSON` — recommended, GZIP-compressed JSON array

### Attribution Windows

Metrics come in multiple attribution windows (suffix indicates days):

| Suffix | Window | Best for |
|--------|--------|----------|
| `1d`   | 1 day  | Quick conversions |
| `7d`   | 7 days | Standard reporting |
| `14d`  | 14 days | Sponsored Brands / Display default |
| `30d`  | 30 days | Long consideration cycles |

### Date Ranges

- Maximum date range: 31 days per request
- Data availability: Reports are typically available 48-72 hours after the end date
- Minimum start date: 60 days in the past

### Example: Full Campaign Report Request

```json
{
  "name": "SP Campaign Report March 2026",
  "startDate": "2026-03-01",
  "endDate": "2026-03-15",
  "configuration": {
    "adProduct": "SPONSORED_PRODUCTS",
    "groupBy": ["campaign"],
    "columns": [
      "date",
      "campaignName",
      "campaignId",
      "campaignStatus",
      "campaignBudgetAmount",
      "impressions",
      "clicks",
      "cost",
      "purchases1d",
      "purchases7d",
      "purchases14d",
      "purchases30d",
      "sales1d",
      "sales7d",
      "sales14d",
      "sales30d",
      "unitsSoldClicks1d",
      "unitsSoldClicks7d",
      "unitsSoldClicks14d",
      "unitsSoldClicks30d"
    ],
    "reportTypeId": "spCampaigns",
    "format": "GZIP_JSON",
    "timeUnit": "DAILY"
  }
}
```
