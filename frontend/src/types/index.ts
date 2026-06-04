export interface Client {
  id: string;
  name: string;
  marketplaces: string[];
  is_active: boolean;
  sp_api_secret_name?: string;
  ads_api_secret_name?: string;
  ads_profile_id?: string;
  created_at?: string;
  updated_at?: string;
}

export interface ScheduleConfig {
  type: ScheduleType;
  time?: string;
  days_of_week?: number[];
  day_of_month?: number;
}

export type TimeframeStrategy =
  | "yesterday"
  | "today"
  | "last_n_days"
  | "rolling_window"
  | "last_calendar_week"
  | "last_calendar_month"
  | "prior_year_window";

export interface Timeframe {
  strategy: TimeframeStrategy;
  days?: number;
  end_offset_days?: number;
  start_offset?: number;
  end_offset?: number;
  week_start?: number;
  days_before?: number;
  days_after?: number;
  years_back?: number;
  anchor_offset_days?: number;
}

export interface Schedule {
  id: string;
  name?: string;
  client_ids: string[];
  api_source: ApiSource;
  report_types: string[];
  marketplaces: string[];
  frequency: Frequency;
  schedule_config: ScheduleConfig;
  timeframe?: Timeframe;
  folder_name: string;
  subfolder_strategy: "date" | "none";
  reconciliation_days: number[];
  report_params: Record<string, unknown>;
  is_active: boolean;
  last_run_at?: string;
  last_run_status?: "success" | "partial" | "failed";
  last_run_job_count?: { completed: number; failed: number; total: number };
  last_drive_folder_id?: string;
  next_run_at?: string;
  created_at?: string;
  updated_at?: string;
}

export interface Job {
  id: string;
  client_id: string;
  schedule_id?: string;
  execution_date?: string;
  status: JobStatus;
  api_source: ApiSource;
  report_type: string;
  marketplace: string;
  amazon_report_id?: string;
  gdrive_file_id?: string;
  gdrive_folder_id?: string;
  gdrive_path?: string;
  error_details?: { message?: string; phase?: string; code?: string };
  retry_count: number;
  poll_count: number;
  frequency?: string;
  report_date?: string;
  report_end_date?: string;
  trigger?: string;
  started_at?: string;
  completed_at?: string;
}

export type ApiSource = "sp_api" | "ads_api" | "both";
export type Frequency = "hourly" | "daily" | "weekly" | "monthly";
export type ScheduleType = "hourly" | "daily" | "weekly" | "monthly";
export type JobStatus =
  | "pending"
  | "requesting"
  | "polling"
  | "downloading"
  | "uploading"
  | "completed"
  | "failed";

export const API_SOURCES: { value: ApiSource; label: string }[] = [
  { value: "sp_api", label: "SP API" },
  { value: "ads_api", label: "Ads API" },
  { value: "both", label: "Both" },
];

export const FREQUENCIES: { value: Frequency; label: string }[] = [
  { value: "hourly", label: "Hourly" },
  { value: "daily", label: "Daily" },
  { value: "weekly", label: "Weekly" },
  { value: "monthly", label: "Monthly" },
];

export const DAYS_OF_WEEK = [
  { value: 0, label: "Mon" },
  { value: 1, label: "Tue" },
  { value: 2, label: "Wed" },
  { value: 3, label: "Thu" },
  { value: 4, label: "Fri" },
  { value: 5, label: "Sat" },
  { value: 6, label: "Sun" },
] as const;

export const TIMEFRAME_STRATEGIES = [
  { value: "yesterday", label: "Yesterday" },
  { value: "today", label: "Today" },
  { value: "last_n_days", label: "Last N Days" },
  { value: "rolling_window", label: "Rolling Window" },
  { value: "last_calendar_week", label: "Last Calendar Week" },
  { value: "last_calendar_month", label: "Last Calendar Month" },
  { value: "prior_year_window", label: "Prior Year Window" },
] as const;

export const MARKETPLACE_TO_REGION: Record<string, string> = {
  US: "na", CA: "na", MX: "na",
  UK: "eu", DE: "eu", FR: "eu", IT: "eu", ES: "eu", NL: "eu", SE: "eu", PL: "eu", TR: "eu",
  AU: "fe", SG: "fe",
};

export const REGION_LABELS: Record<string, string> = {
  na: "NA", eu: "EU", fe: "FE",
};

export function getClientRegions(marketplaces: string[]): string[] {
  return [...new Set(marketplaces.map((m) => MARKETPLACE_TO_REGION[m]).filter(Boolean))];
}

export const MARKETPLACES: { id: string; label: string; flag: string }[] = [
  { id: "US", label: "United States", flag: "\u{1F1FA}\u{1F1F8}" },
  { id: "CA", label: "Canada", flag: "\u{1F1E8}\u{1F1E6}" },
  { id: "MX", label: "Mexico", flag: "\u{1F1F2}\u{1F1FD}" },
  { id: "UK", label: "United Kingdom", flag: "\u{1F1EC}\u{1F1E7}" },
  { id: "DE", label: "Germany", flag: "\u{1F1E9}\u{1F1EA}" },
  { id: "FR", label: "France", flag: "\u{1F1EB}\u{1F1F7}" },
  { id: "IT", label: "Italy", flag: "\u{1F1EE}\u{1F1F9}" },
  { id: "ES", label: "Spain", flag: "\u{1F1EA}\u{1F1F8}" },
  { id: "AU", label: "Australia", flag: "\u{1F1E6}\u{1F1FA}" },
];

export const SP_REPORT_TYPES = [
  "GET_FLAT_FILE_OPEN_LISTINGS_DATA",
  "GET_MERCHANT_LISTINGS_ALL_DATA",
  "GET_FLAT_FILE_ALL_ORDERS_DATA_BY_LAST_UPDATE_GENERAL",
  "GET_FLAT_FILE_ALL_ORDERS_DATA_BY_ORDER_DATE_GENERAL",
  "GET_SALES_AND_TRAFFIC_REPORT",
  "GET_FBA_MYI_UNSUPPRESSED_INVENTORY_DATA",
  "GET_FBA_MYI_ALL_INVENTORY_DATA",
  "GET_FBA_ESTIMATED_FBA_FEES_TXT_DATA",
  "GET_AFN_INVENTORY_DATA",
  "GET_RESTOCK_INVENTORY_RECOMMENDATIONS_REPORT",
  "GET_STRANDED_INVENTORY_UI_DATA",
  "GET_LEDGER_SUMMARY_VIEW_DATA",
  "GET_V2_SETTLEMENT_REPORT_DATA_FLAT_FILE_V2",
  "GET_AMAZON_FULFILLED_SHIPMENTS_DATA_GENERAL",
  "GET_FLAT_FILE_RETURNS_DATA_BY_RETURN_DATE",
  "GET_FBA_FULFILLMENT_CUSTOMER_RETURNS_DATA",
  "GET_FBA_FULFILLMENT_REMOVAL_SHIPMENT_DETAIL_DATA",
  "GET_BRAND_ANALYTICS_SEARCH_TERMS_REPORT",
  "GET_BRAND_ANALYTICS_MARKET_BASKET_REPORT",
  "GET_BRAND_ANALYTICS_REPEAT_PURCHASE_REPORT",
  "GET_BRAND_ANALYTICS_SEARCH_QUERY_PERFORMANCE_REPORT",
  "GET_BRAND_ANALYTICS_SEARCH_CATALOG_PERFORMANCE_REPORT",
  "GET_MERCHANT_LISTINGS_DATA",
  "GET_FBA_INVENTORY_PLANNING_DATA",
  "GET_FBA_SNS_PERFORMANCE_DATA",
] as const;

export const ADS_REPORT_TYPES = [
  "spCampaigns",
  "spSearchTerm",
  "spTargeting",
  "spAdvertisedProduct",
  "sbCampaigns",
  "sbSearchTerm",
  "sdCampaigns",
  "sdTargeting",
  "sdAdvertisedProduct",
  "spPlacement",
] as const;

export interface AdsProfile {
  profileId: number;
  countryCode: string;
  currencyCode: string;
  dailyBudget: number;
  timezone: string;
  accountInfo: {
    marketplaceStringId: string;
    id: string;
    type: string;
    name?: string;
    sellerStringId?: string;
  };
  _linked_client_id?: string;
  _region?: "na" | "eu" | "fe";
}

export interface SpApiAccount {
  id: string;
  name: string;
  marketplaces: string[];
  sp_api_connected: boolean;
  /** Currently saved/linked Ads Profile ID (if any). */
  ads_profile_id?: string | null;
  /** Ads Profile ID(s) discovered live via the Ads API listProfiles endpoint. */
  ads_profiles: AdsProfile[];
  /** Per-account profile-discovery error, if the lazy fetch failed. */
  ads_profiles_error?: string | null;
}

export interface AdsReportColumns {
  dimensions: string[];
  metrics: string[];
}

export interface AdsReportConfig {
  adProduct: string;
  groupBy: string[];
  columns: AdsReportColumns;
  timeUnits: string[];
}

export type AdsReportConfigMap = Record<string, AdsReportConfig>;

export function isAdsReportType(reportType: string): boolean {
  return (ADS_REPORT_TYPES as readonly string[]).includes(reportType);
}

// ---------------------------------------------------------------------------
// Events & Bot Configs (Slack Bots)
// ---------------------------------------------------------------------------

export type EventStatus = "upcoming" | "live" | "completed";

export interface Event {
  id: string;
  name: string;
  start_date: string;
  end_date: string;
  status: EventStatus;
  prior_event_id?: string;
  manually_activated?: boolean;
  activated_at?: string;
  created_at?: string;
  updated_at?: string;
}

export interface BotConfig {
  id: string;
  client_id: string;
  slack_channel_id: string;
  slack_channel_name?: string;
  base_currency: string;
  client_timezone: string;
  marketplaces: string[];
  hourly_bot: { enabled: boolean };
  test_channel_id?: string;
  use_test_channel?: boolean;
  created_at?: string;
  updated_at?: string;
}

export const CURRENCIES = [
  "USD", "CAD", "MXN", "GBP", "EUR", "AUD", "SGD", "SEK", "PLN",
] as const;

export const CLIENT_TIMEZONES = [
  { value: "America/Los_Angeles", label: "Pacific (PST)" },
  { value: "America/Denver", label: "Mountain (MST)" },
  { value: "America/Chicago", label: "Central (CST)" },
  { value: "America/New_York", label: "Eastern (EST)" },
  { value: "Europe/London", label: "London (GMT)" },
  { value: "Europe/Paris", label: "Paris (CET)" },
  { value: "Australia/Sydney", label: "Sydney (AEST)" },
  { value: "Asia/Singapore", label: "Singapore (SGT)" },
] as const;
