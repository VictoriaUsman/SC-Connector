import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { StatusBadge } from "@/components/status-badge";
import { EmptyState } from "@/components/empty-state";
import { MultiSelectDropdown } from "@/components/multi-select-dropdown";
import { FolderConfig } from "@/components/folder-config";
import { ReportSelector } from "@/components/report-selector";
import type { AdsReportParams } from "@/components/ads-report-config";
import { useClients } from "@/hooks/use-clients";
import { useRealtimeJobs, useTriggerReport } from "@/hooks/use-jobs";
import { formatApiSource, formatReportType, timeAgo } from "@/lib/format";
import type { ApiSource, Job } from "@/types";
import { Loader2, Zap, Send, FileText, ExternalLink } from "lucide-react";
import { toast } from "sonner";

function JobProgressCard({ job }: { job: Job }) {
  const steps = ["pending", "requesting", "polling", "downloading", "uploading", "completed"] as const;
  const currentIdx = steps.indexOf(job.status as (typeof steps)[number]);

  return (
    <div className="rounded-lg border p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div className="space-y-0.5">
          <p className="text-sm font-medium">{formatReportType(job.report_type)}</p>
          <p className="text-xs text-muted-foreground">
            {formatApiSource(job.api_source)} &middot; {job.marketplace} &middot; {timeAgo(job.started_at)}
          </p>
        </div>
        <StatusBadge status={job.status} />
      </div>

      {job.status !== "failed" && (
        <div className="flex items-center gap-1">
          {steps.slice(0, -1).map((step, i) => (
            <div
              key={step}
              className={`h-1.5 flex-1 rounded-full transition-colors ${
                i <= currentIdx
                  ? job.status === "completed"
                    ? "bg-emerald-500"
                    : "bg-primary"
                  : "bg-muted"
              }`}
            />
          ))}
        </div>
      )}

      {job.status === "failed" && job.error_details?.message && (
        <p className="text-xs text-destructive">{job.error_details.message}</p>
      )}

      {job.gdrive_path && (
        <p className="text-xs text-muted-foreground">
          Saved to:{" "}
          {job.gdrive_file_id ? (
            <a
              href={`https://drive.google.com/file/d/${job.gdrive_file_id}/view`}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-primary hover:underline"
            >
              {job.gdrive_path}
              <ExternalLink className="h-3 w-3 shrink-0" />
            </a>
          ) : (
            job.gdrive_path
          )}
        </p>
      )}
    </div>
  );
}

export function OnDemand() {
  const { data: clients, isLoading: clientsLoading } = useClients();
  const triggerReport = useTriggerReport();

  const [clientIds, setClientIds] = useState<string[]>([]);
  const [apiSource, setApiSource] = useState<ApiSource>("sp_api");
  const [marketplaceIds, setMarketplaceIds] = useState<string[]>([]);
  const [reportType, setReportType] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [folderName, setFolderName] = useState("");
  const [subfolderStrategy, setSubfolderStrategy] = useState<"date" | "none">("date");
  const [adsConfig, setAdsConfig] = useState<AdsReportParams>({});
  const [submitting, setSubmitting] = useState(false);

  const { jobs: recentJobs, loading: jobsLoading } = useRealtimeJobs({
    max: 10,
  });

  const onDemandJobs = recentJobs.filter((j) => j.frequency === "on_demand");

  const activeClients = (clients ?? []).filter((c) => c.is_active);
  const clientOptions = activeClients.map((c) => ({ id: c.id, label: c.name }));

  const canSubmit = clientIds.length > 0 && marketplaceIds.length > 0 && reportType && !submitting;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);

    const reportParams: Record<string, unknown> = {};
    if (apiSource === "sp_api") {
      if (startDate) reportParams.dataStartTime = startDate;
      if (endDate) reportParams.dataEndTime = endDate;
    } else {
      if (startDate) reportParams.startDate = startDate;
      if (endDate) reportParams.endDate = endDate;
      if (adsConfig.columns) reportParams.columns = adsConfig.columns;
      if (adsConfig.timeUnit) reportParams.timeUnit = adsConfig.timeUnit;
    }

    const pairs = clientIds.flatMap((cid) =>
      marketplaceIds.map((mid) => ({ client_id: cid, marketplace: mid })),
    );

    try {
      const results = await Promise.all(
        pairs.map((pair) =>
          triggerReport.mutateAsync({
            ...pair,
            api_source: apiSource,
            report_type: reportType,
            report_params: reportParams,
            folder_name: folderName || undefined,
            subfolder_strategy: subfolderStrategy,
          }),
        ),
      );
      toast.success(`Triggered ${results.length} report${results.length > 1 ? "s" : ""}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to trigger reports");
    } finally {
      setSubmitting(false);
    }
  };

  if (clientsLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold tracking-tight">On-Demand Report</h1>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Zap className="h-5 w-5" />
              Trigger Report
            </CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="space-y-4">
              <MultiSelectDropdown
                label="Clients"
                options={clientOptions}
                selected={clientIds}
                onChange={setClientIds}
              />

              <ReportSelector
                apiSource={apiSource}
                onApiSourceChange={setApiSource}
                reportType={reportType}
                onReportTypeChange={setReportType}
                marketplaceIds={marketplaceIds}
                onMarketplaceIdsChange={setMarketplaceIds}
                adsConfig={adsConfig}
                onAdsConfigChange={setAdsConfig}
              />

              <Separator />

              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label>Start Date (optional)</Label>
                  <Input
                    type="date"
                    value={startDate}
                    onChange={(e) => setStartDate(e.target.value)}
                  />
                </div>
                <div className="space-y-2">
                  <Label>End Date (optional)</Label>
                  <Input
                    type="date"
                    value={endDate}
                    onChange={(e) => setEndDate(e.target.value)}
                  />
                </div>
              </div>

              <FolderConfig
                folderName={folderName}
                onFolderNameChange={setFolderName}
                subfolderStrategy={subfolderStrategy}
                onSubfolderStrategyChange={setSubfolderStrategy}
              />

              <Button type="submit" className="w-full" disabled={!canSubmit}>
                {submitting ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <Send className="mr-2 h-4 w-4" />
                )}
                {clientIds.length > 0 && marketplaceIds.length > 0
                  ? `Generate ${clientIds.length * marketplaceIds.length} Report${clientIds.length * marketplaceIds.length > 1 ? "s" : ""}`
                  : "Generate Report"}
              </Button>
            </form>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <FileText className="h-5 w-5" />
              Recent On-Demand Jobs
            </CardTitle>
          </CardHeader>
          <CardContent>
            {jobsLoading ? (
              <div className="flex items-center justify-center py-10">
                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
              </div>
            ) : onDemandJobs.length === 0 ? (
              <EmptyState
                icon={FileText}
                title="No on-demand jobs"
                description="Trigger a report and track its progress here in real time."
              />
            ) : (
              <div className="space-y-3">
                {onDemandJobs.map((job) => (
                  <JobProgressCard key={job.id} job={job} />
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
