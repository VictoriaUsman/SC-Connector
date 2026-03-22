import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { getSpReportMeta } from "@/data/report-metadata";
import { useAdsReportConfig } from "@/hooks/use-ads-report-config";
import { isAdsReportType } from "@/types";
import { formatReportType } from "@/lib/format";
import { ChevronRight, Columns3 } from "lucide-react";
import { cn } from "@/lib/utils";

export function ReportColumnsPreview({
  reportType,
}: {
  reportType: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const { data: adsConfigMap } = useAdsReportConfig();

  const isAds = isAdsReportType(reportType);
  let columns: string[] = [];
  let format: string | undefined;

  if (isAds) {
    const cfg = adsConfigMap?.[reportType];
    if (cfg) {
      columns = [...cfg.columns.dimensions, ...cfg.columns.metrics];
    }
  } else {
    const meta = getSpReportMeta(reportType);
    if (meta) {
      columns = meta.columns;
      format = meta.format.toUpperCase();
    }
  }

  if (!columns.length) return null;

  return (
    <div className="rounded-md border bg-muted/30">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-accent/50 transition-colors rounded-md"
      >
        <ChevronRight
          className={cn(
            "h-3 w-3 shrink-0 text-muted-foreground transition-transform",
            expanded && "rotate-90",
          )}
        />
        <Columns3 className="h-3 w-3 shrink-0 text-muted-foreground" />
        <span className="text-xs font-medium flex-1 truncate">
          {formatReportType(reportType)}
        </span>
        <span className="text-xs text-muted-foreground tabular-nums">
          {columns.length} cols
        </span>
        {format && (
          <Badge variant="outline" className="text-[10px] px-1 py-0 shrink-0">
            {format}
          </Badge>
        )}
      </button>

      {expanded && (
        <div className="px-3 pb-2 pt-1 border-t">
          <div className="flex flex-wrap gap-1">
            {columns.map((col) => (
              <Badge
                key={col}
                variant="secondary"
                className="text-[10px] px-1.5 py-0 font-mono"
              >
                {col}
              </Badge>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
