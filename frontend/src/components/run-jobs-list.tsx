import { useMemo, useState } from "react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/status-badge";
import { formatReportType, formatApiSource } from "@/lib/format";
import {
  CheckCircle,
  XCircle,
  Loader2,
  ChevronRight,
  AlertCircle,
  ExternalLink,
  User,
  Info,
  RefreshCw,
  Zap,
} from "lucide-react";
import type { Job, JobStatus } from "@/types";
import { useRealtimeJobs, useRunJobs, useRetryJob } from "@/hooks/use-jobs";

const IN_PROGRESS_STATUSES: JobStatus[] = [
  "pending",
  "requesting",
  "polling",
  "downloading",
  "uploading",
];

function isNoDataError(raw: string): boolean {
  const lower = raw.toLowerCase();
  return (
    lower.includes("no data available") ||
    lower.includes("fatal") && !lower.includes("invalid") ||
    lower.includes("cancelled") && lower.includes("no data")
  );
}

function isThrottledError(raw: string): boolean {
  const lower = raw.toLowerCase();
  return lower.includes("throttled") || lower.includes("quota") || lower.includes("429");
}

function extractErrorSummary(raw: string | undefined): { message: string; kind: "no_data" | "throttled" | "error" } {
  if (!raw) return { message: "Unknown error", kind: "error" };

  if (isNoDataError(raw)) {
    return {
      message: "No data available \u2014 Amazon could not generate this report. The account may lack access (e.g. Brand Registry) or there is no data for the requested date range.",
      kind: "no_data",
    };
  }

  if (isThrottledError(raw)) {
    return {
      message: "Rate limited by Amazon \u2014 too many concurrent requests. Use the retry button to try again.",
      kind: "throttled",
    };
  }

  let msg = raw;
  const detailMatch = raw.match(/'detail':\s*'([^']+)'/);
  if (detailMatch) msg = detailMatch[1];
  else {
    const msgMatch = raw.match(/'message':\s*'([^']+)'/);
    if (msgMatch) msg = msgMatch[1];
  }

  const allowedIdx = msg.indexOf("Allowed values:");
  if (allowedIdx > 0) msg = msg.slice(0, allowedIdx).trimEnd();
  if (msg.endsWith(".")) return { message: msg, kind: "error" };
  if (msg.length > 180) return { message: msg.slice(0, 180).trimEnd() + "\u2026", kind: "error" };
  return { message: msg, kind: "error" };
}

function SummaryBar({ jobs }: { jobs: Job[] }) {
  const completed = jobs.filter((j) => j.status === "completed").length;
  const failed = jobs.filter((j) => j.status === "failed").length;
  const inProgress = jobs.filter((j) => IN_PROGRESS_STATUSES.includes(j.status)).length;

  return (
    <div className="flex items-center gap-3 text-xs text-muted-foreground px-1 py-1.5">
      <span className="font-medium text-foreground">{jobs.length} jobs</span>
      {completed > 0 && (
        <span className="inline-flex items-center gap-1">
          <CheckCircle className="h-3 w-3 text-emerald-500" />
          {completed} completed
        </span>
      )}
      {failed > 0 && (
        <span className="inline-flex items-center gap-1">
          <XCircle className="h-3 w-3 text-destructive" />
          {failed} failed
        </span>
      )}
      {inProgress > 0 && (
        <span className="inline-flex items-center gap-1">
          <Loader2 className="h-3 w-3 animate-spin" />
          {inProgress} in progress
        </span>
      )}
    </div>
  );
}

interface ClientGroup {
  clientId: string;
  jobs: Job[];
  completed: number;
  failed: number;
  inProgress: number;
}

function groupByClient(jobs: Job[]): ClientGroup[] {
  const map = new Map<string, Job[]>();
  for (const job of jobs) {
    const arr = map.get(job.client_id);
    if (arr) arr.push(job);
    else map.set(job.client_id, [job]);
  }
  const groups: ClientGroup[] = [];
  for (const [clientId, clientJobs] of map) {
    groups.push({
      clientId,
      jobs: clientJobs,
      completed: clientJobs.filter((j) => j.status === "completed").length,
      failed: clientJobs.filter((j) => j.status === "failed").length,
      inProgress: clientJobs.filter((j) => IN_PROGRESS_STATUSES.includes(j.status)).length,
    });
  }
  return groups;
}

const COL_COUNT = 7;

function JobRow({ job }: { job: Job }) {
  const [expanded, setExpanded] = useState(false);
  const canExpand = job.status === "failed" && !!job.error_details?.message;
  const retryMutation = useRetryJob();

  const folderId = job.gdrive_folder_id;
  const fileId = job.gdrive_file_id;
  const driveHref = folderId
    ? `https://drive.google.com/drive/folders/${folderId}`
    : fileId
      ? `https://drive.google.com/file/d/${fileId}/view`
      : null;

  const errorInfo = canExpand ? extractErrorSummary(job.error_details?.message) : null;

  return (
    <>
      <TableRow className="text-xs">
        <TableCell className="w-7 pr-0">
          {canExpand ? (
            <button
              type="button"
              onClick={() => setExpanded(!expanded)}
              className="p-0.5 rounded hover:bg-accent transition-colors"
            >
              <ChevronRight
                className={`h-3 w-3 text-muted-foreground transition-transform ${expanded ? "rotate-90" : ""}`}
              />
            </button>
          ) : (
            <span className="inline-block w-4" />
          )}
        </TableCell>
        <TableCell><StatusBadge status={job.status} /></TableCell>
        <TableCell>
          <span className="max-w-[180px] truncate block" title={job.report_type}>
            {formatReportType(job.report_type)}
          </span>
        </TableCell>
        <TableCell>{job.marketplace}</TableCell>
        <TableCell>{formatApiSource(job.api_source)}</TableCell>
        <TableCell>{job.report_date ?? "\u2014"}</TableCell>
        <TableCell>
          {driveHref ? (
            <a
              href={driveHref}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-primary hover:underline"
              title={job.gdrive_path}
            >
              {folderId ? "Folder" : "File"}
              <ExternalLink className="h-3 w-3" />
            </a>
          ) : (
            <span className="text-muted-foreground">{"\u2014"}</span>
          )}
        </TableCell>
      </TableRow>
      {expanded && errorInfo && (
        <TableRow
          className={
            errorInfo.kind === "no_data"
              ? "bg-muted/40 hover:bg-muted/40"
              : errorInfo.kind === "throttled"
                ? "bg-amber-500/5 hover:bg-amber-500/5"
                : "bg-destructive/5 hover:bg-destructive/5"
          }
        >
          <TableCell colSpan={COL_COUNT} className="p-0">
            <div className="px-4 py-2.5 pl-9">
              <div className="flex items-start gap-2.5">
                <div
                  className={`rounded-full p-1 shrink-0 mt-0.5 ${
                    errorInfo.kind === "no_data"
                      ? "bg-muted-foreground/10"
                      : errorInfo.kind === "throttled"
                        ? "bg-amber-500/10"
                        : "bg-destructive/10"
                  }`}
                >
                  {errorInfo.kind === "no_data" ? (
                    <Info className="h-3 w-3 text-muted-foreground" />
                  ) : errorInfo.kind === "throttled" ? (
                    <Zap className="h-3 w-3 text-amber-500" />
                  ) : (
                    <AlertCircle className="h-3 w-3 text-destructive" />
                  )}
                </div>
                <div className="min-w-0 space-y-1.5 flex-1">
                  {job.error_details?.phase && errorInfo.kind === "error" && (
                    <Badge
                      variant="outline"
                      className="text-[10px] font-mono px-1.5 py-0 border-destructive/30 text-destructive"
                    >
                      {job.error_details.phase}
                    </Badge>
                  )}
                  <p
                    className={`text-xs leading-relaxed ${
                      errorInfo.kind === "no_data"
                        ? "text-muted-foreground"
                        : "text-foreground/80"
                    }`}
                  >
                    {errorInfo.message}
                  </p>
                </div>
                {errorInfo.kind !== "no_data" && (
                  <Button
                    variant="outline"
                    size="sm"
                    className="shrink-0 h-7 text-xs gap-1"
                    disabled={retryMutation.isPending}
                    onClick={() => retryMutation.mutate(job.id)}
                  >
                    {retryMutation.isPending ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <RefreshCw className="h-3 w-3" />
                    )}
                    {retryMutation.isSuccess ? "Retried" : "Retry"}
                  </Button>
                )}
              </div>
            </div>
          </TableCell>
        </TableRow>
      )}
    </>
  );
}

function ClientGroupHeader({ group, expanded, onToggle }: { group: ClientGroup; expanded: boolean; onToggle: () => void }) {
  return (
    <TableRow className="bg-muted/40 hover:bg-muted/50 cursor-pointer" onClick={onToggle}>
      <TableCell colSpan={COL_COUNT} className="py-2">
        <div className="flex items-center gap-2.5">
          <ChevronRight
            className={`h-3.5 w-3.5 text-muted-foreground transition-transform ${expanded ? "rotate-90" : ""}`}
          />
          <User className="h-3.5 w-3.5 text-muted-foreground" />
          <span className="font-semibold text-xs">{group.clientId}</span>
          <span className="text-[11px] text-muted-foreground">
            {group.jobs.length} job{group.jobs.length !== 1 ? "s" : ""}
          </span>
          {group.completed > 0 && (
            <span className="inline-flex items-center gap-0.5 text-[11px] text-muted-foreground">
              <CheckCircle className="h-3 w-3 text-emerald-500" />
              {group.completed}
            </span>
          )}
          {group.failed > 0 && (
            <span className="inline-flex items-center gap-0.5 text-[11px] text-muted-foreground">
              <XCircle className="h-3 w-3 text-destructive" />
              {group.failed}
            </span>
          )}
          {group.inProgress > 0 && (
            <span className="inline-flex items-center gap-0.5 text-[11px] text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" />
              {group.inProgress}
            </span>
          )}
        </div>
      </TableCell>
    </TableRow>
  );
}

/**
 * Reusable component that renders individual jobs for a single run.
 *
 * Usage modes:
 * - `jobs` prop: render pre-loaded jobs directly
 * - `scheduleId + executionDate`: real-time query for a specific run
 * - `scheduleId` only: real-time query for the most recent jobs of that schedule
 */
export function RunJobsList({
  jobs: preloadedJobs,
  scheduleId,
  executionDate,
}: {
  jobs?: Job[];
  scheduleId?: string;
  executionDate?: string;
}) {
  const useRunQuery = !preloadedJobs && scheduleId && executionDate;
  const useScheduleQuery = !preloadedJobs && scheduleId && !executionDate;

  const { jobs: runJobs, loading: runLoading } = useRunJobs(
    useRunQuery ? scheduleId : undefined,
    useRunQuery ? executionDate : undefined,
  );
  const { jobs: scheduleJobs, loading: scheduleLoading } = useRealtimeJobs(
    useScheduleQuery ? { scheduleId, max: 100 } : undefined,
  );

  const jobs = preloadedJobs ?? (useRunQuery ? runJobs : scheduleJobs);
  const loading = useRunQuery ? runLoading : useScheduleQuery ? scheduleLoading : false;

  const clientGroups = useMemo(() => groupByClient(jobs), [jobs]);
  const multipleClients = clientGroups.length > 1;

  if (loading) {
    return (
      <div className="flex items-center justify-center py-6">
        <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!jobs.length) {
    return (
      <p className="text-xs text-muted-foreground py-4 text-center">No jobs found for this run.</p>
    );
  }

  return (
    <div className="space-y-1">
      <SummaryBar jobs={jobs} />
      <Table>
        <TableHeader>
          <TableRow className="text-xs">
            <TableHead className="w-7" />
            <TableHead>Status</TableHead>
            <TableHead>Report Type</TableHead>
            <TableHead>Marketplace</TableHead>
            <TableHead>Source</TableHead>
            <TableHead>Report Date</TableHead>
            <TableHead>Drive</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {clientGroups.map((group) => (
            <ClientGroupRows key={group.clientId} group={group} showHeader={multipleClients} />
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function ClientGroupRows({ group, showHeader }: { group: ClientGroup; showHeader: boolean }) {
  const [expanded, setExpanded] = useState(true);

  return (
    <>
      {showHeader && (
        <ClientGroupHeader group={group} expanded={expanded} onToggle={() => setExpanded(!expanded)} />
      )}
      {(!showHeader || expanded) && group.jobs.map((job) => (
        <JobRow key={job.id} job={job} />
      ))}
    </>
  );
}
