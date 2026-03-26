import { useState } from "react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { StatusBadge } from "@/components/status-badge";
import { formatReportType, formatApiSource } from "@/lib/format";
import {
  CheckCircle,
  XCircle,
  Loader2,
  ChevronRight,
  AlertCircle,
  ExternalLink,
} from "lucide-react";
import type { Job, JobStatus } from "@/types";
import { useRealtimeJobs, useRunJobs } from "@/hooks/use-jobs";

const IN_PROGRESS_STATUSES: JobStatus[] = [
  "pending",
  "requesting",
  "polling",
  "downloading",
  "uploading",
];

function extractErrorSummary(raw: string | undefined): string {
  if (!raw) return "Unknown error";

  let msg = raw;
  const detailMatch = raw.match(/'detail':\s*'([^']+)'/);
  if (detailMatch) msg = detailMatch[1];
  else {
    const msgMatch = raw.match(/'message':\s*'([^']+)'/);
    if (msgMatch) msg = msgMatch[1];
  }

  const allowedIdx = msg.indexOf("Allowed values:");
  if (allowedIdx > 0) msg = msg.slice(0, allowedIdx).trimEnd();
  if (msg.endsWith(".")) return msg;
  if (msg.length > 180) return msg.slice(0, 180).trimEnd() + "\u2026";
  return msg;
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

function JobRow({ job }: { job: Job }) {
  const [expanded, setExpanded] = useState(false);
  const canExpand = job.status === "failed" && !!job.error_details?.message;

  const folderId = job.gdrive_folder_id;
  const fileId = job.gdrive_file_id;
  const driveHref = folderId
    ? `https://drive.google.com/drive/folders/${folderId}`
    : fileId
      ? `https://drive.google.com/file/d/${fileId}/view`
      : null;

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
        <TableCell className="font-medium">{job.client_id}</TableCell>
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
      {expanded && (
        <TableRow className="bg-destructive/5 hover:bg-destructive/5">
          <TableCell colSpan={8} className="p-0">
            <div className="px-4 py-2.5 pl-9">
              <div className="flex items-start gap-2.5">
                <div className="rounded-full bg-destructive/10 p-1 shrink-0 mt-0.5">
                  <AlertCircle className="h-3 w-3 text-destructive" />
                </div>
                <div className="min-w-0 space-y-1">
                  {job.error_details?.phase && (
                    <Badge
                      variant="outline"
                      className="text-[10px] font-mono px-1.5 py-0 border-destructive/30 text-destructive"
                    >
                      {job.error_details.phase}
                    </Badge>
                  )}
                  <p className="text-xs text-foreground/80 leading-relaxed">
                    {extractErrorSummary(job.error_details?.message)}
                  </p>
                </div>
              </div>
            </div>
          </TableCell>
        </TableRow>
      )}
    </>
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
            <TableHead>Client</TableHead>
            <TableHead>Report Type</TableHead>
            <TableHead>Marketplace</TableHead>
            <TableHead>Source</TableHead>
            <TableHead>Report Date</TableHead>
            <TableHead>Drive</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {jobs.map((job) => (
            <JobRow key={job.id} job={job} />
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
