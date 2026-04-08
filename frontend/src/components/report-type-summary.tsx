import { Badge } from "@/components/ui/badge";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  SP_REPORT_CATEGORIES,
  ADS_REPORT_CATEGORIES,
  type ReportCategory,
} from "@/data/report-categories";
import { isAdsReportType } from "@/types";
import { ChevronDown } from "lucide-react";

interface CategoryGroup {
  label: string;
  reports: string[];
}

function groupByCategory(reportTypes: string[]): {
  sp: CategoryGroup[];
  ads: CategoryGroup[];
} {
  const spIds = new Set(reportTypes.filter((rt) => !isAdsReportType(rt)));
  const adsIds = new Set(reportTypes.filter((rt) => isAdsReportType(rt)));

  const collect = (categories: ReportCategory[], ids: Set<string>) =>
    categories
      .map((cat) => ({
        label: cat.label,
        reports: cat.reports.filter((r) => ids.has(r.id)).map((r) => r.label),
      }))
      .filter((g) => g.reports.length > 0);

  return {
    sp: collect(SP_REPORT_CATEGORIES, spIds),
    ads: collect(ADS_REPORT_CATEGORIES, adsIds),
  };
}

export function ReportTypeSummary({
  reportTypes,
}: {
  reportTypes: string[];
}) {
  if (!reportTypes.length) return <span className="text-muted-foreground">-</span>;

  const { sp, ads } = groupByCategory(reportTypes);
  const spCount = reportTypes.filter((rt) => !isAdsReportType(rt)).length;
  const adsCount = reportTypes.filter((rt) => isAdsReportType(rt)).length;

  const categoryLabels = [...sp, ...ads].map((g) => g.label);
  const previewText =
    categoryLabels.length <= 3
      ? categoryLabels.join(" · ")
      : `${categoryLabels.slice(0, 2).join(" · ")} +${categoryLabels.length - 2}`;

  return (
    <Popover>
      <PopoverTrigger
        render={
          <button
            type="button"
            className="group flex flex-col gap-1 text-left cursor-pointer rounded-md px-1.5 py-1 -mx-1.5 -my-1 hover:bg-accent/60 transition-colors"
          >
            <div className="flex items-center gap-1.5">
              {spCount > 0 && (
                <Badge
                  variant="secondary"
                  className="text-[11px] px-1.5 py-0 bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300"
                >
                  {spCount} SP
                </Badge>
              )}
              {adsCount > 0 && (
                <Badge
                  variant="secondary"
                  className="text-[11px] px-1.5 py-0 bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300"
                >
                  {adsCount} Ads
                </Badge>
              )}
              <ChevronDown className="h-3 w-3 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity" />
            </div>
            <span className="text-[11px] text-muted-foreground leading-tight">
              {previewText}
            </span>
          </button>
        }
      />
      <PopoverContent align="start" className="w-80 p-0">
        <div className="px-3 py-2.5 border-b">
          <p className="text-xs font-medium text-muted-foreground">
            {reportTypes.length} report{reportTypes.length !== 1 ? "s" : ""}
          </p>
        </div>
        <div className="px-3 py-2 space-y-2.5 max-h-72 overflow-y-auto">
          {sp.length > 0 && (
            <div className="space-y-2">
              {sp.length > 0 && ads.length > 0 && (
                <p className="text-[10px] font-semibold uppercase tracking-wider text-blue-600 dark:text-blue-400">
                  SP API
                </p>
              )}
              {sp.map((g) => (
                <div key={g.label}>
                  <p className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground mb-0.5">
                    {g.label}
                  </p>
                  <div className="flex flex-wrap gap-1">
                    {g.reports.map((name) => (
                      <Badge
                        key={name}
                        variant="secondary"
                        className="text-[10px] px-1.5 py-0 font-normal"
                      >
                        {name}
                      </Badge>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
          {ads.length > 0 && (
            <div className="space-y-2">
              {sp.length > 0 && ads.length > 0 && (
                <p className="text-[10px] font-semibold uppercase tracking-wider text-purple-600 dark:text-purple-400 pt-1 border-t">
                  Ads API
                </p>
              )}
              {ads.map((g) => (
                <div key={g.label}>
                  <p className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground mb-0.5">
                    {g.label}
                  </p>
                  <div className="flex flex-wrap gap-1">
                    {g.reports.map((name) => (
                      <Badge
                        key={name}
                        variant="secondary"
                        className="text-[10px] px-1.5 py-0 font-normal"
                      >
                        {name}
                      </Badge>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
