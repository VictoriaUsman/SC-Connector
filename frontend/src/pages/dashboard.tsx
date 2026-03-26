import { useMemo, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/empty-state";
import { RunJobsList } from "@/components/run-jobs-list";
import { useRealtimeJobs } from "@/hooks/use-jobs";
import { useSchedules } from "@/hooks/use-schedules";
import {
  groupJobsBySchedule,
  runStatusLabel,
  runStatusVariant,
  type ScheduleGroup,
  type ScheduleRun,
} from "@/lib/group-jobs";
import { timeAgo, formatApiSource } from "@/lib/format";
import {
  CheckCircle,
  XCircle,
  Loader2,
  Clock,
  Activity,
  FilterX,
  ChevronRight,
  CalendarClock,
  AlertTriangle,
} from "lucide-react";
import type { Job, JobStatus } from "@/types";

const IN_PROGRESS_STATUSES: JobStatus[] = [
  "pending",
  "requesting",
  "polling",
  "downloading",
  "uploading",
];

const STATUS_OPTIONS = [
  { value: "all", label: "All statuses" },
  { value: "completed", label: "Completed" },
  { value: "failed", label: "Failed" },
  { value: "in_progress", label: "In Progress" },
  { value: "partial", label: "Partial" },
];

const TIME_RANGE_OPTIONS = [
  { value: "1", label: "Last hour" },
  { value: "6", label: "Last 6 hours" },
  { value: "24", label: "Last 24 hours" },
  { value: "168", label: "Last 7 days" },
  { value: "720", label: "Last 30 days" },
];

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

function RunStatusIcon({ variant }: { variant: ReturnType<typeof runStatusVariant> }) {
  switch (variant) {
    case "success":
      return <CheckCircle className="h-3.5 w-3.5 text-emerald-500" />;
    case "failed":
      return <XCircle className="h-3.5 w-3.5 text-destructive" />;
    case "partial":
      return <AlertTriangle className="h-3.5 w-3.5 text-amber-500" />;
    case "running":
      return <Loader2 className="h-3.5 w-3.5 animate-spin text-blue-500" />;
  }
}

function RunRow({ run, isLast }: { run: ScheduleRun; isLast: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const variant = runStatusVariant(run);

  return (
    <div className={!isLast && !expanded ? "border-b border-border/50" : ""}>
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-3 px-4 py-2.5 text-sm hover:bg-accent/50 transition-colors text-left"
      >
        <ChevronRight
          className={`h-3.5 w-3.5 text-muted-foreground shrink-0 transition-transform ${expanded ? "rotate-90" : ""}`}
        />
        <RunStatusIcon variant={variant} />
        <span className="font-medium">{run.executionDate}</span>
        <span className="text-xs text-muted-foreground">{runStatusLabel(run)}</span>
        <span className="text-xs text-muted-foreground ml-auto">{timeAgo(run.latestStartedAt)}</span>
      </button>
      {expanded && (
        <div className="border-t border-border/40 bg-muted/30 px-2 pb-3">
          <RunJobsList jobs={run.jobs} />
        </div>
      )}
    </div>
  );
}

function ScheduleGroupCard({ group }: { group: ScheduleGroup }) {
  const [expanded, setExpanded] = useState(false);
  const schedule = group.schedule;
  const latestRun = group.runs[0];
  const latestVariant = latestRun ? runStatusVariant(latestRun) : undefined;

  return (
    <Card className="overflow-hidden">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-accent/30 transition-colors"
      >
        <ChevronRight
          className={`h-4 w-4 text-muted-foreground shrink-0 transition-transform ${expanded ? "rotate-90" : ""}`}
        />
        <div className="flex flex-1 items-center gap-3 min-w-0">
          <CalendarClock className="h-4 w-4 text-muted-foreground shrink-0" />
          <span className="font-semibold text-sm truncate">{group.scheduleName}</span>
          {latestVariant && <RunStatusIcon variant={latestVariant} />}
          {latestRun && (
            <span className="text-xs text-muted-foreground">
              {runStatusLabel(latestRun)}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3 shrink-0">
          {schedule && (
            <Badge variant="outline" className="text-xs">
              {formatApiSource(schedule.api_source)}
            </Badge>
          )}
          <Badge variant="secondary" className="text-xs tabular-nums">
            {group.runs.length} run{group.runs.length !== 1 ? "s" : ""}
          </Badge>
          <Badge variant="secondary" className="text-xs tabular-nums">
            {group.totalJobs} job{group.totalJobs !== 1 ? "s" : ""}
          </Badge>
          <span className="text-xs text-muted-foreground w-16 text-right">
            {timeAgo(group.latestStartedAt)}
          </span>
        </div>
      </button>
      {expanded && (
        <div className="border-t">
          {group.runs.map((run, idx) => (
            <RunRow key={run.executionDate} run={run} isLast={idx === group.runs.length - 1} />
          ))}
        </div>
      )}
    </Card>
  );
}

function AdhocJobsCard({ jobs }: { jobs: Job[] }) {
  const [expanded, setExpanded] = useState(false);

  if (!jobs.length) return null;

  return (
    <Card className="overflow-hidden">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-accent/30 transition-colors"
      >
        <ChevronRight
          className={`h-4 w-4 text-muted-foreground shrink-0 transition-transform ${expanded ? "rotate-90" : ""}`}
        />
        <div className="flex flex-1 items-center gap-3 min-w-0">
          <Activity className="h-4 w-4 text-muted-foreground shrink-0" />
          <span className="font-semibold text-sm">On-Demand</span>
        </div>
        <Badge variant="secondary" className="text-xs tabular-nums">
          {jobs.length} job{jobs.length !== 1 ? "s" : ""}
        </Badge>
      </button>
      {expanded && (
        <div className="border-t bg-muted/30 px-2 pb-3">
          <RunJobsList jobs={jobs} />
        </div>
      )}
    </Card>
  );
}

export function Dashboard() {
  const { jobs, loading: jobsLoading } = useRealtimeJobs({ max: 500 });
  const { data: schedules, isLoading: schedulesLoading } = useSchedules();

  const [filterSchedule, setFilterSchedule] = useState("");
  const [filterStatus, setFilterStatus] = useState("");
  const [filterTimeRange, setFilterTimeRange] = useState("");

  const loading = jobsLoading || schedulesLoading;

  const filteredJobs = useMemo(() => {
    let result = jobs;

    if (filterTimeRange) {
      const hours = Number(filterTimeRange);
      const cutoff = Date.now() - hours * 3_600_000;
      result = result.filter((j) => j.started_at && new Date(j.started_at).getTime() >= cutoff);
    }

    return result;
  }, [jobs, filterTimeRange]);

  const { groups, adhocJobs } = useMemo(
    () => groupJobsBySchedule(filteredJobs, schedules ?? []),
    [filteredJobs, schedules],
  );

  const visibleGroups = useMemo(() => {
    let result = groups;

    if (filterSchedule) {
      result = result.filter((g) => g.scheduleId === filterSchedule);
    }

    if (filterStatus) {
      result = result.filter((g) => {
        const latestRun = g.runs[0];
        if (!latestRun) return false;
        switch (filterStatus) {
          case "completed":
            return latestRun.failed === 0 && latestRun.inProgress === 0;
          case "failed":
            return latestRun.completed === 0 && latestRun.inProgress === 0;
          case "in_progress":
            return latestRun.inProgress > 0;
          case "partial":
            return latestRun.failed > 0 && latestRun.completed > 0 && latestRun.inProgress === 0;
          default:
            return true;
        }
      });
    }

    return result;
  }, [groups, filterSchedule, filterStatus]);

  const stats = useMemo(() => {
    const allJobs = filteredJobs;
    const completed = allJobs.filter((j) => j.status === "completed").length;
    const failed = allJobs.filter((j) => j.status === "failed").length;
    const inProgress = allJobs.filter((j) => IN_PROGRESS_STATUSES.includes(j.status)).length;
    return { completed, failed, inProgress, total: allJobs.length };
  }, [filteredJobs]);

  const hasFilters = !!filterSchedule || !!filterStatus || !!filterTimeRange;
  const statsDescription = hasFilters ? "Filtered results" : "Last 500 jobs";

  const distinctSchedules = useMemo(() => {
    return groups.map((g) => ({ id: g.scheduleId, name: g.scheduleName }));
  }, [groups]);

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
          description={statsDescription}
        />
        <StatCard
          title="Failed"
          value={stats.failed}
          icon={XCircle}
          description={statsDescription}
        />
        <StatCard
          title="In Progress"
          value={stats.inProgress}
          icon={Clock}
          description={statsDescription}
        />
        <StatCard
          title="Total Jobs"
          value={stats.total}
          icon={Activity}
          description={statsDescription}
        />
      </div>

      {/* Filter toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <Select
          value={filterSchedule}
          onValueChange={(v) => setFilterSchedule(v === "all" ? "" : (v ?? ""))}
        >
          <SelectTrigger size="sm" className="w-[200px]">
            <SelectValue placeholder="All schedules" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All schedules</SelectItem>
            {distinctSchedules.map((s) => (
              <SelectItem key={s.id} value={s.id}>
                {s.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select
          value={filterStatus}
          onValueChange={(v) => setFilterStatus(v === "all" ? "" : (v ?? ""))}
        >
          <SelectTrigger size="sm" className="w-[150px]">
            <SelectValue placeholder="All statuses" />
          </SelectTrigger>
          <SelectContent>
            {STATUS_OPTIONS.map((o) => (
              <SelectItem key={o.value} value={o.value}>
                {o.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select
          value={filterTimeRange}
          onValueChange={(v) => setFilterTimeRange(v === "all" ? "" : (v ?? ""))}
        >
          <SelectTrigger size="sm" className="w-[150px]">
            <SelectValue placeholder="Time range" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All time</SelectItem>
            {TIME_RANGE_OPTIONS.map((o) => (
              <SelectItem key={o.value} value={o.value}>
                {o.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {hasFilters && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setFilterSchedule("");
              setFilterStatus("");
              setFilterTimeRange("");
            }}
          >
            <FilterX className="mr-1 h-3.5 w-3.5" />
            Clear
          </Button>
        )}
      </div>

      {/* Schedule groups */}
      {visibleGroups.length === 0 && adhocJobs.length === 0 ? (
        <Card>
          <CardContent className="pt-6">
            <EmptyState
              icon={Activity}
              title={hasFilters ? "No matching runs" : "No jobs yet"}
              description={
                hasFilters
                  ? "Try adjusting your filters."
                  : "Trigger an on-demand report or configure a schedule to get started."
              }
            />
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3">
          {visibleGroups.map((group) => (
            <ScheduleGroupCard key={group.scheduleId} group={group} />
          ))}
          {!filterSchedule && <AdhocJobsCard jobs={adhocJobs} />}
        </div>
      )}
    </div>
  );
}
