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
  isAdsReportType,
} from "@/types";
import type { ApiSource } from "@/types";

const MARKETPLACE_OPTIONS = MARKETPLACES.map((m) => ({
  id: m.id,
  label: `${m.flag} ${m.id}`,
}));

const SP_REPORT_OPTIONS = SP_REPORT_TYPES.map((rt) => ({
  id: rt,
  label: formatReportType(rt),
}));

const ADS_REPORT_OPTIONS = ADS_REPORT_TYPES.map((rt) => ({
  id: rt,
  label: formatReportType(rt),
}));

export function ReportSelector({
  apiSource,
  onApiSourceChange,
  reportTypes,
  onReportTypesChange,
  marketplaceIds,
  onMarketplaceIdsChange,
  reportParamsMap,
  onReportParamsMapChange,
}: {
  apiSource: ApiSource;
  onApiSourceChange: (source: ApiSource) => void;
  reportTypes: string[];
  onReportTypesChange: (types: string[]) => void;
  marketplaceIds: string[];
  onMarketplaceIdsChange: (ids: string[]) => void;
  reportParamsMap: Record<string, Record<string, unknown>>;
  onReportParamsMapChange: (map: Record<string, Record<string, unknown>>) => void;
}) {
  const showSp = apiSource === "sp_api" || apiSource === "both";
  const showAds = apiSource === "ads_api" || apiSource === "both";

  const selectedSpTypes = reportTypes.filter((rt) => !isAdsReportType(rt));
  const selectedAdsTypes = reportTypes.filter((rt) => isAdsReportType(rt));

  const handleSpChange = (ids: string[]) => {
    onReportTypesChange([...ids, ...selectedAdsTypes]);
  };

  const handleAdsChange = (ids: string[]) => {
    const removed = selectedAdsTypes.filter((rt) => !ids.includes(rt));
    if (removed.length) {
      const next = { ...reportParamsMap };
      for (const rt of removed) delete next[rt];
      onReportParamsMapChange(next);
    }
    onReportTypesChange([...selectedSpTypes, ...ids]);
  };

  const handleAdsParamsChange = (rt: string, params: AdsReportParams) => {
    const entry: Record<string, unknown> = {};
    if (params.columns) entry.columns = params.columns;
    if (params.timeUnit) entry.timeUnit = params.timeUnit;
    onReportParamsMapChange({ ...reportParamsMap, [rt]: entry });
  };

  return (
    <>
      <div className="space-y-2">
        <Label>API Source</Label>
        <Select
          value={apiSource}
          onValueChange={(v) => {
            if (!v) return;
            onApiSourceChange(v as ApiSource);
            onReportTypesChange([]);
            onReportParamsMapChange({});
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

      {showSp && (
        <MultiSelectDropdown
          label={apiSource === "both" ? "SP API Report Types" : "Report Types"}
          options={SP_REPORT_OPTIONS}
          selected={selectedSpTypes}
          onChange={handleSpChange}
        />
      )}

      {showAds && (
        <>
          <MultiSelectDropdown
            label={apiSource === "both" ? "Ads API Report Types" : "Report Types"}
            options={ADS_REPORT_OPTIONS}
            selected={selectedAdsTypes}
            onChange={handleAdsChange}
          />

          {selectedAdsTypes.map((rt) => (
            <AdsReportConfigPanel
              key={rt}
              reportType={rt}
              value={
                (reportParamsMap[rt] as AdsReportParams | undefined) ?? {}
              }
              onChange={(params) => handleAdsParamsChange(rt, params)}
            />
          ))}
        </>
      )}

      <MultiSelectDropdown
        label="Marketplaces"
        options={MARKETPLACE_OPTIONS}
        selected={marketplaceIds}
        onChange={onMarketplaceIdsChange}
        searchable={false}
      />
    </>
  );
}
