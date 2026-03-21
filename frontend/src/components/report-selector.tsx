import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { MultiSelectDropdown } from "@/components/multi-select-dropdown";
import { AdsReportConfigPanel, type AdsReportParams } from "@/components/ads-report-config";
import { formatReportType } from "@/lib/format";
import {
  API_SOURCES,
  MARKETPLACES,
  SP_REPORT_TYPES,
  ADS_REPORT_TYPES,
} from "@/types";
import type { ApiSource } from "@/types";

const MARKETPLACE_OPTIONS = MARKETPLACES.map((m) => ({
  id: m.id,
  label: `${m.flag} ${m.id}`,
}));

export function ReportSelector({
  apiSource,
  onApiSourceChange,
  reportType,
  onReportTypeChange,
  marketplaceIds,
  onMarketplaceIdsChange,
  adsConfig,
  onAdsConfigChange,
}: {
  apiSource: ApiSource;
  onApiSourceChange: (source: ApiSource) => void;
  reportType: string;
  onReportTypeChange: (type: string) => void;
  marketplaceIds: string[];
  onMarketplaceIdsChange: (ids: string[]) => void;
  adsConfig: AdsReportParams;
  onAdsConfigChange: (config: AdsReportParams) => void;
}) {
  const reportTypes = apiSource === "sp_api" ? SP_REPORT_TYPES : ADS_REPORT_TYPES;

  return (
    <>
      <div className="space-y-2">
        <Label>API Source</Label>
        <Select
          value={apiSource}
          onValueChange={(v) => {
            if (v) {
              onApiSourceChange(v as ApiSource);
              onReportTypeChange("");
              onAdsConfigChange({});
            }
          }}
        >
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {API_SOURCES.map((s) => (
              <SelectItem key={s.value} value={s.value}>
                {s.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="space-y-2">
        <Label>Report Type</Label>
        <Select
          value={reportType}
          onValueChange={(v) => {
            if (v) {
              onReportTypeChange(v);
              onAdsConfigChange({});
            }
          }}
        >
          <SelectTrigger className="w-full">
            <SelectValue placeholder="Select report type" />
          </SelectTrigger>
          <SelectContent className="w-auto min-w-[var(--anchor-width)]">
            {reportTypes.map((rt) => (
              <SelectItem key={rt} value={rt}>
                {formatReportType(rt)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <MultiSelectDropdown
        label="Marketplaces"
        options={MARKETPLACE_OPTIONS}
        selected={marketplaceIds}
        onChange={onMarketplaceIdsChange}
        searchable={false}
      />

      {apiSource === "ads_api" && reportType && (
        <AdsReportConfigPanel
          reportType={reportType}
          value={adsConfig}
          onChange={onAdsConfigChange}
        />
      )}
    </>
  );
}
