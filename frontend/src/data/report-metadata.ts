/**
 * Static metadata for all report types.
 *
 * SP API: output column names, format, description, configurable reportOptions.
 * Ads API: dimensions + metrics columns per report type (aligned with
 *   functions/shared/ads_report_config.py — the backend also serves these
 *   via /ads-report-config for runtime column pickers).
 */

export interface SpReportOption {
  key: string;
  label: string;
  choices: { value: string; label: string }[];
  default: string;
}

export interface SpReportMeta {
  description: string;
  format: "tsv" | "json";
  columns: string[];
  options?: SpReportOption[];
}

export interface AdsReportMeta {
  description: string;
  adProduct: string;
  dimensions: string[];
  metrics: string[];
}

export const SP_REPORT_METADATA: Record<string, SpReportMeta> = {
  GET_FLAT_FILE_OPEN_LISTINGS_DATA: {
    description: "Active listings with price, quantity, and condition",
    format: "tsv",
    columns: [
      "sku", "asin", "price", "quantity", "business-price",
      "minimum-order-quantity", "maximum-order-quantity",
      "quantity-price-type", "quantity-lower-bound-1",
      "quantity-price-1", "fulfillment-channel",
    ],
  },

  GET_MERCHANT_LISTINGS_ALL_DATA: {
    description: "All listings including inactive, with full details",
    format: "tsv",
    columns: [
      "item-name", "item-description", "listing-id", "seller-sku",
      "price", "quantity", "open-date", "image-url",
      "item-is-marketplace", "product-id-type", "product-id",
      "item-condition", "pending-quantity",
      "fulfillment-channel", "asin1",
    ],
  },

  GET_FLAT_FILE_ALL_ORDERS_DATA_BY_LAST_UPDATE_GENERAL: {
    description: "All orders sorted by last update date",
    format: "tsv",
    columns: [
      "amazon-order-id", "purchase-date", "last-updated-date",
      "order-status", "fulfillment-channel", "sales-channel",
      "product-name", "sku", "asin", "quantity", "currency",
      "item-price", "item-tax", "shipping-price",
      "ship-city", "ship-state", "ship-postal-code", "ship-country",
    ],
  },

  GET_FLAT_FILE_ALL_ORDERS_DATA_BY_ORDER_DATE_GENERAL: {
    description: "All orders sorted by order date",
    format: "tsv",
    columns: [
      "amazon-order-id", "purchase-date", "last-updated-date",
      "order-status", "fulfillment-channel", "sales-channel",
      "product-name", "sku", "asin", "quantity", "currency",
      "item-price", "item-tax", "shipping-price",
      "ship-city", "ship-state", "ship-postal-code", "ship-country",
    ],
  },

  GET_SALES_AND_TRAFFIC_REPORT: {
    description: "Sales and traffic metrics (Brand Analytics)",
    format: "json",
    columns: [
      "date", "parentAsin", "childAsin", "title",
      "sessions", "sessionPercentage", "pageViews", "pageViewsPercentage",
      "buyBoxPercentage", "unitsOrdered", "unitsOrderedB2B",
      "orderedProductSales.amount", "orderedProductSales.currencyCode",
      "totalOrderItems", "totalOrderItemsB2B",
    ],
    options: [
      {
        key: "dateGranularity",
        label: "Date Granularity",
        choices: [
          { value: "DAY", label: "Day" },
          { value: "WEEK", label: "Week" },
          { value: "MONTH", label: "Month" },
        ],
        default: "DAY",
      },
      {
        key: "asinGranularity",
        label: "ASIN Granularity",
        choices: [
          { value: "CHILD", label: "Child ASIN" },
          { value: "PARENT", label: "Parent ASIN" },
          { value: "SKU", label: "SKU" },
        ],
        default: "CHILD",
      },
    ],
  },

  GET_FBA_MYI_UNSUPPRESSED_INVENTORY_DATA: {
    description: "FBA inventory levels (unsuppressed items)",
    format: "tsv",
    columns: [
      "sku", "fnsku", "asin", "product-name", "condition",
      "your-price", "mfn-listing-exists", "mfn-fulfillable-quantity",
      "afn-listing-exists", "afn-warehouse-quantity",
      "afn-fulfillable-quantity", "afn-unsellable-quantity",
      "afn-reserved-quantity", "afn-total-quantity",
      "afn-inbound-working-quantity", "afn-inbound-shipped-quantity",
      "afn-inbound-receiving-quantity",
    ],
  },

  GET_FBA_ESTIMATED_FBA_FEES_TXT_DATA: {
    description: "Estimated FBA fees per product",
    format: "tsv",
    columns: [
      "sku", "fnsku", "asin", "product-name", "product-group",
      "brand", "fulfilled-by", "your-price", "sales-price",
      "longest-side", "median-side", "shortest-side",
      "item-package-weight", "product-size-tier", "currency",
      "estimated-fee-total", "estimated-referral-fee-per-unit",
      "estimated-variable-closing-fee",
      "estimated-pick-pack-fee-per-unit",
      "estimated-weight-handling-fee-per-unit",
      "expected-fulfillment-fee-per-unit",
    ],
  },

  GET_AFN_INVENTORY_DATA: {
    description: "Amazon Fulfillment Network inventory snapshot",
    format: "tsv",
    columns: [
      "seller-sku", "fulfillment-channel-sku", "asin",
      "condition-type", "Warehouse-Condition-code", "Quantity Available",
    ],
  },

  GET_LEDGER_SUMMARY_VIEW_DATA: {
    description: "Financial ledger summary by ASIN",
    format: "json",
    columns: [
      "Date", "ASIN", "MSKU", "Title", "Disposition",
      "Starting Warehouse Balance", "Ending Warehouse Balance",
      "Receipts", "Customer Shipments", "Customer Returns",
      "Vendor Returns", "Warehouse Transfer In/Out",
      "Found", "Lost", "Damaged", "Disposed",
      "Other Events", "Unknown Events",
    ],
  },

  GET_V2_SETTLEMENT_REPORT_DATA_FLAT_FILE_V2: {
    description: "Settlement report with transaction details",
    format: "tsv",
    columns: [
      "settlement-id", "settlement-start-date", "settlement-end-date",
      "deposit-date", "total-amount", "currency",
      "transaction-type", "order-id", "shipment-id",
      "marketplace-name", "amount-type", "amount-description",
      "amount", "fulfillment-id", "posted-date",
      "sku", "quantity-purchased", "promotion-id",
    ],
  },

  GET_AMAZON_FULFILLED_SHIPMENTS_DATA_GENERAL: {
    description: "FBA shipment details including tracking and item info",
    format: "tsv",
    columns: [
      "amazon-order-id", "merchant-order-id", "shipment-id",
      "shipment-item-id", "amazon-order-item-id",
      "purchase-date", "payments-date", "shipment-date",
      "reporting-date", "buyer-email", "buyer-name",
      "buyer-phone-number", "sku", "product-name", "quantity-shipped",
      "currency", "item-price", "item-tax",
      "shipping-price", "shipping-tax",
      "ship-service-level", "recipient-name",
      "ship-address-1", "ship-city", "ship-state",
      "ship-postal-code", "ship-country",
      "tracking-number", "carrier", "asin",
      "fulfillment-center-id",
    ],
  },

  GET_FLAT_FILE_RETURNS_DATA_BY_RETURN_DATE: {
    description: "FBM (merchant-fulfilled) returns by return date",
    format: "tsv",
    columns: [
      "return-date", "order-id", "sku", "asin",
      "fnsku", "product-name", "quantity",
      "fulfillment-center-id", "detailed-disposition",
      "reason", "status", "license-plate-number",
      "customer-comments",
    ],
  },

  GET_FBA_FULFILLMENT_CUSTOMER_RETURNS_DATA: {
    description: "FBA customer returns with reason and disposition",
    format: "tsv",
    columns: [
      "return-date", "order-id", "sku", "asin",
      "fnsku", "product-name", "quantity",
      "fulfillment-center-id", "detailed-disposition",
      "reason", "status", "license-plate-number",
      "customer-comments",
    ],
  },

  GET_FBA_FULFILLMENT_REMOVAL_SHIPMENT_DETAIL_DATA: {
    description: "FBA removal shipment details (returns to seller or disposal)",
    format: "tsv",
    columns: [
      "request-date", "order-id", "order-type",
      "order-status", "last-updated-date", "sku",
      "fnsku", "disposition", "shipped-quantity",
      "cancelled-quantity", "disposed-quantity",
      "ship-to-city", "ship-to-state", "ship-to-country",
      "carrier", "tracking-number", "shipment-date",
    ],
  },

  GET_BRAND_ANALYTICS_SEARCH_TERMS_REPORT: {
    description: "Brand Analytics search terms with click and conversion share",
    format: "json",
    columns: [
      "departmentName", "searchTerm", "searchFrequencyRank",
      "clickedAsin", "clickShareRank", "clickShare",
      "conversionShare",
    ],
    options: [
      {
        key: "reportPeriod",
        label: "Report Period",
        choices: [
          { value: "DAY", label: "Day" },
          { value: "WEEK", label: "Week" },
          { value: "MONTH", label: "Month" },
          { value: "QUARTER", label: "Quarter" },
        ],
        default: "DAY",
      },
    ],
  },

  GET_BRAND_ANALYTICS_MARKET_BASKET_REPORT: {
    description: "Market basket analysis — products frequently bought together",
    format: "json",
    columns: [
      "departmentName", "asin", "title",
      "combinationAsin", "combinationTitle",
      "combinationPercentage",
    ],
    options: [
      {
        key: "reportPeriod",
        label: "Report Period",
        choices: [
          { value: "WEEK", label: "Week" },
          { value: "MONTH", label: "Month" },
          { value: "QUARTER", label: "Quarter" },
        ],
        default: "MONTH",
      },
    ],
  },

  GET_BRAND_ANALYTICS_REPEAT_PURCHASE_REPORT: {
    description: "Repeat purchase behavior — customer order frequency by ASIN",
    format: "json",
    columns: [
      "departmentName", "asin", "title",
      "ordersCount", "uniqueCustomersCount",
      "repeatCustomersCount", "repeatCustomersPctTotal",
    ],
    options: [
      {
        key: "reportPeriod",
        label: "Report Period",
        choices: [
          { value: "WEEK", label: "Week" },
          { value: "MONTH", label: "Month" },
          { value: "QUARTER", label: "Quarter" },
        ],
        default: "MONTH",
      },
    ],
  },

  GET_BRAND_ANALYTICS_SEARCH_QUERY_PERFORMANCE_REPORT: {
    description: "Search query performance — impressions, clicks, cart adds, purchases per query per ASIN",
    format: "json",
    columns: [
      "asin", "searchQuery", "searchQueryScore", "searchQueryVolume",
      "totalQueryImpressionCount", "asinImpressionCount", "asinImpressionShare",
      "totalClickCount", "totalClickRate", "asinClickCount", "asinClickShare",
      "totalCartAddCount", "totalCartAddRate", "asinCartAddCount", "asinCartAddShare",
      "totalPurchaseCount", "totalPurchaseRate", "asinPurchaseCount", "asinPurchaseShare",
    ],
    options: [
      {
        key: "reportPeriod",
        label: "Report Period",
        choices: [
          { value: "WEEK", label: "Week" },
          { value: "MONTH", label: "Month" },
          { value: "QUARTER", label: "Quarter" },
        ],
        default: "MONTH",
      },
    ],
  },

  GET_BRAND_ANALYTICS_SEARCH_CATALOG_PERFORMANCE_REPORT: {
    description: "Search catalog performance — impressions, clicks, cart adds, purchases per ASIN across all queries",
    format: "json",
    columns: [
      "asin", "impressionCount", "impressionMedianPrice",
      "clickCount", "clickRate", "clickedMedianPrice",
      "cartAddCount", "cartAddedMedianPrice",
      "purchaseCount", "searchTrafficSales", "conversionRate", "purchaseMedianPrice",
      "sameDayShippingImpressionCount", "oneDayShippingImpressionCount", "twoDayShippingImpressionCount",
    ],
    options: [
      {
        key: "reportPeriod",
        label: "Report Period",
        choices: [
          { value: "WEEK", label: "Week" },
          { value: "MONTH", label: "Month" },
          { value: "QUARTER", label: "Quarter" },
        ],
        default: "MONTH",
      },
    ],
  },
};

/**
 * Ads API report metadata — mirrors functions/shared/ads_report_config.py.
 * Keep in sync when adding/changing Ads report types or columns.
 *
 * Validated against Amazon Ads API v3 (Apr 2026).
 */
export const ADS_REPORT_METADATA: Record<string, AdsReportMeta> = {
  spCampaigns: {
    description: "Campaign-level spend, clicks, and sales with 1/7/14/30d attribution windows",
    adProduct: "SPONSORED_PRODUCTS",
    dimensions: [
      "date", "campaignName", "campaignId", "campaignStatus",
      "campaignBudgetAmount", "campaignBudgetType",
    ],
    metrics: [
      "impressions", "clicks", "cost",
      "purchases1d", "purchases7d", "purchases14d", "purchases30d",
      "sales1d", "sales7d", "sales14d", "sales30d",
      "unitsSoldClicks1d", "unitsSoldClicks7d", "unitsSoldClicks14d", "unitsSoldClicks30d",
    ],
  },
  spSearchTerm: {
    description: "Search term performance with keyword targeting details",
    adProduct: "SPONSORED_PRODUCTS",
    dimensions: [
      "date", "searchTerm", "campaignName", "campaignId",
      "adGroupName", "adGroupId", "targeting", "keywordId", "keywordType",
    ],
    metrics: [
      "impressions", "clicks", "cost",
      "purchases7d", "sales7d", "unitsSoldClicks7d",
    ],
  },
  spTargeting: {
    description: "Keyword/targeting performance by keyword, match type, and ad group",
    adProduct: "SPONSORED_PRODUCTS",
    dimensions: [
      "date", "targeting", "keyword", "keywordId", "keywordType", "matchType",
      "campaignName", "campaignId", "adGroupName", "adGroupId",
    ],
    metrics: [
      "impressions", "clicks", "cost", "costPerClick",
      "purchases7d", "sales7d", "unitsSoldClicks7d",
      "topOfSearchImpressionShare",
    ],
  },
  spAdvertisedProduct: {
    description: "ASIN-level performance per campaign and ad group",
    adProduct: "SPONSORED_PRODUCTS",
    dimensions: [
      "date", "advertisedAsin", "advertisedSku",
      "campaignName", "campaignId", "adGroupName", "adGroupId",
    ],
    metrics: [
      "impressions", "clicks", "cost",
      "purchases7d", "sales7d", "unitsSoldClicks7d",
    ],
  },
  sbCampaigns: {
    description: "Campaign-level metrics including new-to-brand and detail page views",
    adProduct: "SPONSORED_BRANDS",
    dimensions: [
      "date", "campaignName", "campaignId", "campaignStatus",
      "campaignBudgetAmount",
    ],
    metrics: [
      "impressions", "clicks", "cost",
      "purchases", "sales", "unitsSoldClicks",
      "detailPageViewsClicks", "newToBrandPurchases", "newToBrandSales",
    ],
  },
  sbSearchTerm: {
    description: "Search term performance for Sponsored Brands campaigns",
    adProduct: "SPONSORED_BRANDS",
    dimensions: [
      "date", "searchTerm", "campaignName", "campaignId",
      "adGroupName", "adGroupId",
    ],
    metrics: [
      "impressions", "clicks", "cost",
      "purchases", "sales",
    ],
  },
  sdCampaigns: {
    description: "Campaign-level metrics including new-to-brand and detail page views",
    adProduct: "SPONSORED_DISPLAY",
    dimensions: [
      "date", "campaignName", "campaignId", "campaignStatus",
      "campaignBudgetAmount",
    ],
    metrics: [
      "impressions", "clicks", "cost",
      "purchases", "sales", "unitsSoldClicks",
      "detailPageViewsClicks", "newToBrandPurchases", "newToBrandSales",
    ],
  },
  sdTargeting: {
    description: "Targeting-level performance using targetingText for display targeting",
    adProduct: "SPONSORED_DISPLAY",
    dimensions: [
      "date", "targetingText",
      "campaignName", "campaignId", "adGroupName", "adGroupId",
    ],
    metrics: [
      "impressions", "clicks", "cost",
      "purchases", "sales", "unitsSoldClicks",
      "detailPageViewsClicks", "newToBrandSalesClicks",
    ],
  },
  sdAdvertisedProduct: {
    description: "ASIN-level performance using promotedAsin/promotedSku for display ads",
    adProduct: "SPONSORED_DISPLAY",
    dimensions: [
      "date", "promotedAsin", "promotedSku",
      "campaignName", "campaignId", "adGroupName", "adGroupId",
    ],
    metrics: [
      "impressions", "clicks", "cost",
      "purchases", "sales", "unitsSoldClicks",
      "detailPageViewsClicks", "newToBrandPurchases", "newToBrandSales",
    ],
  },
};

export function getSpReportMeta(reportType: string): SpReportMeta | undefined {
  return SP_REPORT_METADATA[reportType];
}

export function getAdsReportMeta(reportType: string): AdsReportMeta | undefined {
  return ADS_REPORT_METADATA[reportType];
}

export function hasSpReportOptions(reportType: string): boolean {
  return !!SP_REPORT_METADATA[reportType]?.options?.length;
}
