export function timeAgo(isoString: string | undefined): string {
  if (!isoString) return "-";
  const ms = new Date(isoString).getTime();
  if (Number.isNaN(ms)) return "-";
  const diff = Date.now() - ms;
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export function formatDate(isoString: string | undefined): string {
  if (!isoString) return "-";
  return new Date(isoString).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatApiSource(source: string): string {
  if (source === "sp_api") return "SP API";
  if (source === "ads_api") return "Ads API";
  if (source === "both") return "Both";
  return source;
}

const ADS_REPORT_LABELS: Record<string, string> = {
  spCampaigns: "SP - Campaigns",
  spSearchTerm: "SP - Search Term",
  spTargeting: "SP - Targeting",
  spAdvertisedProduct: "SP - Advertised Product",
  spPlacement: "SP - Placement",
  sbCampaigns: "SB - Campaigns",
  sbSearchTerm: "SB - Search Term",
  sdCampaigns: "SD - Campaigns",
  sdTargeting: "SD - Targeting",
  sdAdvertisedProduct: "SD - Advertised Product",
};

export function formatReportType(type: string): string {
  if (ADS_REPORT_LABELS[type]) return ADS_REPORT_LABELS[type];
  return type
    .replace(/^GET_/, "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .substring(0, 40);
}

const DOW_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export function formatTimeframeLabel(
  tf: { strategy: string; days?: number; end_offset_days?: number; start_offset?: number; end_offset?: number; week_start?: number } | undefined,
): string {
  if (!tf) return "Yesterday";
  switch (tf.strategy) {
    case "yesterday":
      return "Yesterday";
    case "today":
      return "Today";
    case "last_n_days": {
      const days = tf.days ?? 30;
      const delay = tf.end_offset_days ?? 0;
      return delay > 0 ? `Last ${days}d (delay \u2212${delay})` : `Last ${days} days`;
    }
    case "rolling_window":
      return `Window ${tf.start_offset ?? -7} to ${tf.end_offset ?? -1}`;
    case "last_calendar_week": {
      const ws = tf.week_start ?? 0;
      return `Weekly ${DOW_LABELS[ws]}\u2013${DOW_LABELS[(ws + 6) % 7]}`;
    }
    case "last_calendar_month":
      return "Last calendar month";
    default:
      return "Yesterday";
  }
}
