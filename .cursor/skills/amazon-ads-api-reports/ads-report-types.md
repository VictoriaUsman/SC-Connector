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

**Key columns (validated against API Apr 2026):**
- Dimensions: `targeting`, `keyword`, `keywordId`, `keywordType`, `matchType`, `campaignName`, `campaignId`, `adGroupName`, `adGroupId`
- Metrics: `impressions`, `clicks`, `cost`, `costPerClick`, `purchases7d`, `sales7d`, `unitsSoldClicks7d`, `topOfSearchImpressionShare`
- Time: `date`

**NOT valid:** `targetingId`, `targetingType` (these don't exist in v3 SP targeting reports)

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

**Key columns (validated against API Apr 2026):**
- Dimensions: `campaignName`, `campaignId`, `campaignStatus`, `campaignBudgetAmount`
- Metrics: `impressions`, `clicks`, `cost`, `purchases`, `sales`, `unitsSoldClicks`, `detailPageViewsClicks`, `newToBrandPurchases`, `newToBrandSales`
- Time: `date`

**Note:** SD uses non-suffixed metrics (not `purchases14d`). View-through metrics like `viewImpressions`, `viewAttributedSales14d` are NOT valid in v3.

### sdTargeting

Targeting-level performance for Sponsored Display.

**groupBy options:** `targeting`

**Key columns (validated against API Apr 2026):**
- Dimensions: `targetingText`, `campaignName`, `campaignId`, `adGroupName`, `adGroupId`
- Metrics: `impressions`, `clicks`, `cost`, `purchases`, `sales`, `unitsSoldClicks`, `detailPageViewsClicks`, `newToBrandSalesClicks`
- Time: `date`

**NOT valid:** `targeting` (use `targetingText` for SD), `targetingId` (not available for SD targeting)

### sdAdvertisedProduct

ASIN-level performance for Sponsored Display.

**groupBy options:** `advertiser`

**Key columns (validated against API Apr 2026):**
- Dimensions: `promotedAsin`, `promotedSku`, `campaignName`, `campaignId`, `adGroupName`, `adGroupId`
- Metrics: `impressions`, `clicks`, `cost`, `purchases`, `sales`, `unitsSoldClicks`, `detailPageViewsClicks`, `newToBrandPurchases`, `newToBrandSales`
- Time: `date`

**NOT valid:** `advertisedAsin` (use `promotedAsin`), `advertisedSku` (use `promotedSku`), `viewImpressions`, `viewAttributedSales14d`, `viewAttributedPurchases14d` (v2 columns, not available in v3)

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
