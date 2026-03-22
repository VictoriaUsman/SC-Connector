/**
 * Static metadata for SP API report types.
 *
 * Includes output column names, format, description, and any
 * configurable reportOptions that the SP API accepts.
 *
 * Ads API metadata comes from the backend via /ads-report-config.
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
};

export function getSpReportMeta(reportType: string): SpReportMeta | undefined {
  return SP_REPORT_METADATA[reportType];
}

export function hasSpReportOptions(reportType: string): boolean {
  return !!SP_REPORT_METADATA[reportType]?.options?.length;
}
