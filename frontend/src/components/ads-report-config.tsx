import { useEffect, useMemo, useState } from "react";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { useAdsReportConfig } from "@/hooks/use-ads-report-config";
import type { AdsReportConfig } from "@/types";
import { ChevronRight, Settings2 } from "lucide-react";
import { cn } from "@/lib/utils";

export interface AdsReportParams {
  columns?: string[];
  timeUnit?: string;
}

export function AdsReportConfigPanel({
  reportType,
  value,
  onChange,
}: {
  reportType: string;
  value: AdsReportParams;
  onChange: (params: AdsReportParams) => void;
}) {
  const { data: configMap, isLoading } = useAdsReportConfig();
  const config: AdsReportConfig | undefined = configMap?.[reportType];
  const [expanded, setExpanded] = useState(false);

  const allColumns = useMemo(() => {
    if (!config) return [];
    return [...config.columns.dimensions, ...config.columns.metrics];
  }, [config]);

  useEffect(() => {
    if (!config) return;
    if (!value.columns) {
      onChange({ ...value, columns: allColumns, timeUnit: value.timeUnit ?? "DAILY" });
    }
  }, [reportType, config]);

  if (!reportType) return null;
  if (isLoading) {
    return <p className="text-xs text-muted-foreground">Loading report config...</p>;
  }
  if (!config) return null;

  const isSummary = (value.timeUnit ?? "DAILY") === "SUMMARY";
  const selectedColumns = value.columns ?? allColumns;
  const allSelected = selectedColumns.length === allColumns.length;
  const timeUnitLabel = (value.timeUnit ?? "DAILY") === "SUMMARY" ? "Summary" : "Daily";

  const toggleColumn = (col: string) => {
    const next = selectedColumns.includes(col)
      ? selectedColumns.filter((c) => c !== col)
      : [...selectedColumns, col];
    onChange({ ...value, columns: next });
  };

  const selectAll = (group: string[]) => {
    const missing = group.filter((c) => !selectedColumns.includes(c));
    if (missing.length > 0) {
      onChange({ ...value, columns: [...selectedColumns, ...missing] });
    } else {
      onChange({ ...value, columns: selectedColumns.filter((c) => !group.includes(c)) });
    }
  };

  const renderColumnGroup = (label: string, columns: string[]) => {
    const allChecked = columns.every((c) => selectedColumns.includes(c));

    return (
      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
            {label}
          </span>
          <button
            type="button"
            onClick={() => selectAll(columns)}
            className="text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            {allChecked ? "Clear" : "Select all"}
          </button>
        </div>
        <div className="grid grid-cols-2 gap-1">
          {columns.map((col) => (
            <label
              key={col}
              className="flex items-center gap-2 cursor-pointer rounded px-1.5 py-0.5 hover:bg-accent text-xs"
            >
              <Checkbox
                checked={selectedColumns.includes(col)}
                onCheckedChange={() => toggleColumn(col)}
              />
              <span className="truncate" title={col}>{col}</span>
            </label>
          ))}
        </div>
      </div>
    );
  };

  return (
    <div className="rounded-md border">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-accent/50 transition-colors rounded-md"
      >
        <ChevronRight
          className={cn(
            "h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform",
            expanded && "rotate-90",
          )}
        />
        <Settings2 className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <span className="text-sm font-medium flex-1 truncate">
          {config.adProduct.replace("SPONSORED_", "").replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase())}
        </span>
        <span className="text-xs text-muted-foreground">
          {allSelected ? "All" : `${selectedColumns.length}/${allColumns.length}`} columns
        </span>
        <Badge variant="outline" className="text-xs px-1.5 py-0 shrink-0">
          {timeUnitLabel}
        </Badge>
      </button>

      {expanded && (
        <div className="space-y-3 px-3 pb-3 pt-1 border-t">
          <div className="space-y-2">
            <Label className="text-xs">Time Unit</Label>
            <Select
              value={value.timeUnit ?? "DAILY"}
              onValueChange={(v) => {
                const unit = v ?? undefined;
                const cols = unit === "SUMMARY"
                  ? selectedColumns.filter((c) => c !== "date")
                  : selectedColumns;
                onChange({ ...value, timeUnit: unit, columns: cols });
              }}
            >
              <SelectTrigger className="h-8 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {config.timeUnits.map((tu) => (
                  <SelectItem key={tu} value={tu} className="text-xs">
                    {tu === "DAILY" ? "Daily (one row per day)" : "Summary (aggregated)"}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label className="text-xs">
                Columns ({selectedColumns.length}/{allColumns.length})
              </Label>
            </div>
            <div className="space-y-3 max-h-48 overflow-y-auto pr-1">
              {renderColumnGroup(
                "Dimensions",
                isSummary
                  ? config.columns.dimensions.filter((c) => c !== "date")
                  : config.columns.dimensions,
              )}
              {renderColumnGroup("Metrics", config.columns.metrics)}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
