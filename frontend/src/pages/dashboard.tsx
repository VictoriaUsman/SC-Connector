import { useMemo } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { StatusBadge } from "@/components/status-badge";
import { EmptyState } from "@/components/empty-state";
import { useRealtimeJobs } from "@/hooks/use-jobs";
import { timeAgo, formatApiSource, formatReportType } from "@/lib/format";
import {
  CheckCircle,
  XCircle,
  Loader2,
  Clock,
  Activity,
  ExternalLink,
} from "lucide-react";
import type { Job, JobStatus } from "@/types";

function StatCard({
  title,
  value,
  icon: Icon,
  description,
}: {
  title: string;
  value: number;
  icon: React.ElementType;
  description: string;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
        <Icon className="h-4 w-4 text-muted-foreground" />
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold">{value}</div>
        <p className="text-xs text-muted-foreground">{description}</p>
      </CardContent>
    </Card>
  );
}

const IN_PROGRESS_STATUSES: JobStatus[] = ["pending", "requesting", "polling", "downloading", "uploading"];

export function Dashboard() {
  const { jobs, loading } = useRealtimeJobs({ max: 100 });

  const stats = useMemo(() => {
    const completed = jobs.filter((j) => j.status === "completed").length;
    const failed = jobs.filter((j) => j.status === "failed").length;
    const inProgress = jobs.filter((j) => IN_PROGRESS_STATUSES.includes(j.status)).length;
    return { completed, failed, inProgress, total: jobs.length };
  }, [jobs]);

  const recentJobs = useMemo(() => jobs.slice(0, 20), [jobs]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold tracking-tight">Dashboard</h1>

      <div className="grid gap-4 md:grid-cols-4">
        <StatCard
          title="Completed"
          value={stats.completed}
          icon={CheckCircle}
          description="Successful reports"
        />
        <StatCard
          title="Failed"
          value={stats.failed}
          icon={XCircle}
          description="Reports with errors"
        />
        <StatCard
          title="In Progress"
          value={stats.inProgress}
          icon={Clock}
          description="Currently running"
        />
        <StatCard
          title="Total Jobs"
          value={stats.total}
          icon={Activity}
          description="Last 100 jobs"
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Recent Activity</CardTitle>
        </CardHeader>
        <CardContent>
          {recentJobs.length === 0 ? (
            <EmptyState
              icon={Activity}
              title="No jobs yet"
              description="Trigger an on-demand report or configure a schedule to get started."
            />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Status</TableHead>
                  <TableHead>Client</TableHead>
                  <TableHead>Source</TableHead>
                  <TableHead>Report Type</TableHead>
                  <TableHead>Marketplace</TableHead>
                  <TableHead>Drive</TableHead>
                  <TableHead className="text-right">Started</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {recentJobs.map((job: Job) => (
                  <TableRow key={job.id}>
                    <TableCell>
                      <StatusBadge status={job.status} />
                    </TableCell>
                    <TableCell className="font-medium">{job.client_id}</TableCell>
                    <TableCell>{formatApiSource(job.api_source)}</TableCell>
                    <TableCell className="max-w-[200px] truncate" title={job.report_type}>
                      {formatReportType(job.report_type)}
                    </TableCell>
                    <TableCell>{job.marketplace}</TableCell>
                    <TableCell>
                      {job.gdrive_file_id ? (
                        <a
                          href={`https://drive.google.com/file/d/${job.gdrive_file_id}/view`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1 text-primary hover:underline text-xs"
                          title={job.gdrive_path}
                        >
                          Open
                          <ExternalLink className="h-3 w-3" />
                        </a>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </TableCell>
                    <TableCell className="text-right text-muted-foreground">
                      {timeAgo(job.started_at)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
