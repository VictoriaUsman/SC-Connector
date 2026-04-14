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
    description: "Active listings with price, quantity, and B2B pricing tiers",
    format: "tsv",
    columns: [
      "sku", "asin", "price", "quantity",
      "Business Price", "Quantity Price Type",
      "Quantity Lower Bound 1", "Quantity Price 1",
      "Quantity Lower Bound 2", "Quantity Price 2",
      "Quantity Lower Bound 3", "Quantity Price 3",
      "Quantity Lower Bound 4", "Quantity Price 4",
      "Quantity Lower Bound 5", "Quantity Price 5",
      "Progressive Price Type",
      "Progressive Lower Bound 1", "Progressive Price 1",
      "Progressive Lower Bound 2", "Progressive Price 2",
      "Progressive Lower Bound 3", "Progressive Price 3",
    ],
  },

  GET_MERCHANT_LISTINGS_ALL_DATA: {
    description: "All listings including inactive, with full details",
    format: "tsv",
    columns: [
      "item-name", "item-description", "listing-id", "seller-sku",
      "price", "quantity", "open-date", "image-url",
      "item-is-marketplace", "product-id-type",
      "zshop-shipping-fee", "item-note", "item-condition",
      "zshop-category1", "zshop-browse-path", "zshop-storefront-feature",
      "asin1", "asin2", "asin3",
      "will-ship-internationally", "expedited-shipping", "zshop-boldface",
      "product-id", "bid-for-featured-placement", "add-delete",
      "pending-quantity", "fulfillment-channel",
      "merchant-shipping-group", "status",
    ],
  },

  GET_FLAT_FILE_ALL_ORDERS_DATA_BY_LAST_UPDATE_GENERAL: {
    description: "All orders sorted by last update date",
    format: "tsv",
    columns: [
      "amazon-order-id", "merchant-order-id",
      "purchase-date", "last-updated-date",
      "order-status", "fulfillment-channel", "sales-channel", "order-channel",
      "ship-service-level", "product-name", "sku", "asin", "item-status",
      "quantity", "currency", "item-price", "item-tax",
      "shipping-price", "shipping-tax",
      "gift-wrap-price", "gift-wrap-tax",
      "item-promotion-discount", "ship-promotion-discount",
      "ship-city", "ship-state", "ship-postal-code", "ship-country",
      "promotion-ids", "is-business-order",
      "purchase-order-number", "price-designation",
    ],
  },

  GET_FLAT_FILE_ALL_ORDERS_DATA_BY_ORDER_DATE_GENERAL: {
    description: "All orders sorted by order date",
    format: "tsv",
    columns: [
      "amazon-order-id", "merchant-order-id",
      "purchase-date", "last-updated-date",
      "order-status", "fulfillment-channel", "sales-channel", "order-channel",
      "ship-service-level", "product-name", "sku", "asin", "item-status",
      "quantity", "currency", "item-price", "item-tax",
      "shipping-price", "shipping-tax",
      "gift-wrap-price", "gift-wrap-tax",
      "item-promotion-discount", "ship-promotion-discount",
      "ship-city", "ship-state", "ship-postal-code", "ship-country",
      "promotion-ids", "cpf", "is-business-order",
      "purchase-order-number", "price-designation",
    ],
  },

  GET_SALES_AND_TRAFFIC_REPORT: {
    description: "Sales and traffic metrics per ASIN and per date (Brand Analytics)",
    format: "json",
    columns: [
      "date", "parentAsin", "childAsin", "sku",
      "orderedProductSales.amount", "orderedProductSalesB2B.amount",
      "unitsOrdered", "unitsOrderedB2B",
      "totalOrderItems", "totalOrderItemsB2B",
      "averageSalesPerOrderItem.amount", "averageSellingPrice.amount",
      "averageUnitsPerOrderItem",
      "unitsRefunded", "refundRate",
      "claimsGranted", "claimsAmount.amount",
      "shippedProductSales.amount", "unitsShipped", "ordersShipped",
      "browserPageViews", "mobileAppPageViews", "pageViews",
      "browserSessions", "mobileAppSessions", "sessions",
      "browserSessionPercentage", "mobileAppSessionPercentage", "sessionPercentage",
      "browserPageViewsPercentage", "mobileAppPageViewsPercentage", "pageViewsPercentage",
      "buyBoxPercentage", "unitSessionPercentage",
      "orderItemSessionPercentage",
      "averageOfferCount", "averageParentItems",
      "feedbackReceived", "negativeFeedbackReceived", "receivedNegativeFeedbackRate",
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
    description: "FBA inventory levels for active (unsuppressed) items",
    format: "tsv",
    columns: [
      "sku", "fnsku", "asin", "product-name", "condition",
      "your-price", "mfn-listing-exists", "mfn-fulfillable-quantity",
      "afn-listing-exists", "afn-warehouse-quantity",
      "afn-fulfillable-quantity", "afn-unsellable-quantity",
      "afn-reserved-quantity", "afn-total-quantity",
      "per-unit-volume",
      "afn-inbound-working-quantity", "afn-inbound-shipped-quantity",
      "afn-inbound-receiving-quantity",
      "afn-researching-quantity", "afn-reserved-future-supply",
      "afn-future-supply-buyable",
    ],
  },

  GET_FBA_ESTIMATED_FBA_FEES_TXT_DATA: {
    description: "Estimated Amazon selling and fulfillment fees per FBA product",
    format: "tsv",
    columns: [
      "sku", "fnsku", "asin", "product-name", "product-group",
      "brand", "fulfilled-by", "has-local-inventory",
      "your-price", "sales-price",
      "longest-side", "median-side", "shortest-side",
      "length-and-girth", "unit-of-dimension",
      "item-package-weight", "unit-of-weight",
      "product-size-weight-band", "currency",
      "estimated-fee-total", "estimated-referral-fee-per-unit",
      "estimated-variable-closing-fee",
      "expected-domestic-fulfilment-fee-per-unit",
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
    description: "Inventory ledger summary — reconciliation of FBA inventory movements",
    format: "tsv",
    columns: [
      "Date", "FNSKU", "ASIN", "MSKU", "Title", "Disposition",
      "StartingWarehouseBalance", "InTransitBetweenWarehouses",
      "Receipts", "CustomerShipments", "CustomerReturns",
      "VendorReturns", "WarehouseTransferIn/Out",
      "Found", "Lost", "Damaged", "Disposed",
      "OtherEvents", "EndingWarehouseBalance",
      "UnknownEvents", "Location",
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
    description: "FBA shipment details including tracking, pricing, and address info",
    format: "tsv",
    columns: [
      "amazon-order-id", "merchant-order-id", "shipment-id",
      "shipment-item-id", "amazon-order-item-id", "merchant-order-item-id",
      "purchase-date", "payments-date", "shipment-date", "reporting-date",
      "buyer-email", "buyer-name", "buyer-phone-number",
      "sku", "product-name", "quantity-shipped",
      "currency", "item-price", "item-tax",
      "shipping-price", "shipping-tax",
      "gift-wrap-price", "gift-wrap-tax",
      "ship-service-level", "recipient-name",
      "ship-address-1", "ship-address-2", "ship-address-3",
      "ship-city", "ship-state", "ship-postal-code", "ship-country",
      "ship-phone-number",
      "bill-address-1", "bill-address-2", "bill-address-3",
      "bill-city", "bill-state", "bill-postal-code", "bill-country",
      "item-promotion-discount", "ship-promotion-discount",
      "carrier", "tracking-number", "estimated-arrival-date",
      "fulfillment-center-id", "fulfillment-channel", "sales-channel",
    ],
  },

  GET_FLAT_FILE_RETURNS_DATA_BY_RETURN_DATE: {
    description: "FBM (merchant-fulfilled) returns with RMA, label, and refund details",
    format: "tsv",
    columns: [
      "Order ID", "Order date", "Return request date", "Return request status",
      "Amazon RMA ID", "Merchant RMA ID",
      "Label type", "Label cost", "Currency code",
      "Return carrier", "Tracking ID", "Label to be paid by",
      "A-to-Z Claim", "Is prime",
      "ASIN", "Merchant SKU", "Item Name",
      "Return quantity", "Return Reason", "In policy",
      "Return type", "Resolution",
      "Invoice number", "Return delivery date",
      "Order Amount", "Order quantity",
      "SafeT Action reason", "SafeT claim id", "SafeT claim state",
      "SafeT claim creation time", "SafeT claim reimbursement amount",
      "Refunded Amount",
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
    description: "FBA removal shipment tracking for all removal orders",
    format: "tsv",
    columns: [
      "request-date", "order-id", "shipment-date",
      "sku", "fnsku", "disposition",
      "shipped-quantity", "carrier", "tracking-number",
      "removal-order-type",
    ],
  },

  GET_MERCHANT_LISTINGS_DATA: {
    description: "Active merchant-fulfilled listings with inventory quantities",
    format: "tsv",
    columns: [
      "item-name", "item-description", "listing-id", "seller-sku",
      "price", "quantity", "open-date", "image-url",
      "item-is-marketplace", "product-id-type",
      "zshop-shipping-fee", "item-note", "item-condition",
      "zshop-category1", "zshop-browse-path", "zshop-storefront-feature",
      "asin1", "asin2", "asin3",
      "will-ship-internationally", "expedited-shipping", "zshop-boldface",
      "product-id", "bid-for-featured-placement", "add-delete",
      "pending-quantity", "fulfillment-channel",
      "merchant-shipping-group", "status",
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
  spPlacement: {
    description: "Placement-level performance: top of search, product pages, and other placements",
    adProduct: "SPONSORED_PRODUCTS",
    dimensions: [
      "date", "campaignName", "campaignId",
      "placementClassification", "campaignBiddingStrategy",
    ],
    metrics: [
      "impressions", "clicks", "cost", "costPerClick",
      "purchases1d", "purchases7d", "purchases14d", "purchases30d",
      "sales1d", "sales7d", "sales14d", "sales30d",
      "unitsSoldClicks1d", "unitsSoldClicks7d", "unitsSoldClicks14d", "unitsSoldClicks30d",
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
