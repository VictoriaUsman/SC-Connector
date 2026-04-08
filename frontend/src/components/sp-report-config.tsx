import { useState, useEffect } from "react";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { getSpReportMeta } from "@/data/report-metadata";
import type { SpReportOption } from "@/data/report-metadata";
import { formatReportType } from "@/lib/format";
import { ChevronRight, Settings2 } from "lucide-react";
import { cn } from "@/lib/utils";

export interface SpReportParams {
  reportOptions?: Record<string, string>;
}

export function SpReportConfigPanel({
  reportType,
  value,
  onChange,
}: {
  reportType: string;
  value: SpReportParams;
  onChange: (params: SpReportParams) => void;
}) {
  const meta = getSpReportMeta(reportType);
  const options = meta?.options;
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (!options?.length) return;
    if (value.reportOptions) return;
    const defaults: Record<string, string> = {};
    for (const opt of options) {
      defaults[opt.key] = opt.default;
    }
    onChange({ ...value, reportOptions: defaults });
  }, [reportType, options]);

  if (!options?.length) return null;

  const currentOptions = value.reportOptions ?? {};
  const summaryParts = options.map(
    (opt) =>
      opt.choices.find((c) => c.value === (currentOptions[opt.key] ?? opt.default))?.label ??
      opt.default,
  );

  const handleOptionChange = (opt: SpReportOption, val: string) => {
    onChange({
      ...value,
      reportOptions: { ...currentOptions, [opt.key]: val },
    });
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
          {formatReportType(reportType)}
        </span>
        <span className="text-xs text-muted-foreground">{summaryParts.join(" · ")}</span>
      </button>

      {expanded && (
        <div className="space-y-3 px-3 pb-3 pt-1 border-t">
          {options.map((opt) => (
            <div key={opt.key} className="space-y-1.5">
              <Label className="text-xs">{opt.label}</Label>
              <Select
                value={currentOptions[opt.key] ?? opt.default}
                onValueChange={(v) => v && handleOptionChange(opt, v)}
              >
                <SelectTrigger className="h-8 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {opt.choices.map((c) => (
                    <SelectItem key={c.value} value={c.value} className="text-xs">
                      {c.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
