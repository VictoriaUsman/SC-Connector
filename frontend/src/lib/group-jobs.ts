import type { Job, JobStatus, Schedule } from "@/types";

const IN_PROGRESS_STATUSES: JobStatus[] = [
  "pending",
  "requesting",
  "polling",
  "downloading",
  "uploading",
];

export interface ScheduleRun {
  executionDate: string;
  jobs: Job[];
  completed: number;
  failed: number;
  inProgress: number;
  total: number;
  latestStartedAt: string | undefined;
}

export interface ScheduleGroup {
  scheduleId: string;
  scheduleName: string;
  schedule: Schedule | undefined;
  runs: ScheduleRun[];
  totalJobs: number;
  latestStartedAt: string | undefined;
}

function buildRun(executionDate: string, jobs: Job[]): ScheduleRun {
  const completed = jobs.filter((j) => j.status === "completed").length;
  const failed = jobs.filter((j) => j.status === "failed").length;
  const inProgress = jobs.filter((j) => IN_PROGRESS_STATUSES.includes(j.status)).length;
  const latestStartedAt = jobs.reduce<string | undefined>(
    (best, j) => (!best || (j.started_at && j.started_at > best) ? j.started_at : best),
    undefined,
  );
  return { executionDate, jobs, completed, failed, inProgress, total: jobs.length, latestStartedAt };
}

export function groupJobsBySchedule(
  jobs: Job[],
  schedules: Schedule[],
): { groups: ScheduleGroup[]; adhocJobs: Job[] } {
  const scheduleMap = new Map(schedules.map((s) => [s.id, s]));
  const bySchedule = new Map<string, Map<string, Job[]>>();
  const adhocJobs: Job[] = [];

  for (const job of jobs) {
    if (!job.schedule_id || !job.execution_date) {
      adhocJobs.push(job);
      continue;
    }
    let runsMap = bySchedule.get(job.schedule_id);
    if (!runsMap) {
      runsMap = new Map();
      bySchedule.set(job.schedule_id, runsMap);
    }
    let runJobs = runsMap.get(job.execution_date);
    if (!runJobs) {
      runJobs = [];
      runsMap.set(job.execution_date, runJobs);
    }
    runJobs.push(job);
  }

  const groups: ScheduleGroup[] = [];

  for (const [scheduleId, runsMap] of bySchedule) {
    const schedule = scheduleMap.get(scheduleId);
    const runs: ScheduleRun[] = [];
    for (const [executionDate, runJobs] of runsMap) {
      runs.push(buildRun(executionDate, runJobs));
    }
    runs.sort((a, b) => (b.executionDate > a.executionDate ? 1 : -1));

    const totalJobs = runs.reduce((sum, r) => sum + r.total, 0);
    const latestStartedAt = runs[0]?.latestStartedAt;

    groups.push({
      scheduleId,
      scheduleName: schedule?.name || scheduleId.slice(0, 8),
      schedule,
      runs,
      totalJobs,
      latestStartedAt,
    });
  }

  groups.sort((a, b) => {
    const aTime = a.latestStartedAt ?? "";
    const bTime = b.latestStartedAt ?? "";
    return bTime > aTime ? 1 : bTime < aTime ? -1 : 0;
  });

  return { groups, adhocJobs };
}

export function runStatusLabel(run: ScheduleRun): string {
  if (run.inProgress > 0) return `${run.completed}/${run.total} running`;
  if (run.failed === 0) return `${run.completed}/${run.total} done`;
  if (run.completed === 0) return `${run.failed}/${run.total} failed`;
  return `${run.completed} done, ${run.failed} failed`;
}

export function runStatusVariant(run: ScheduleRun): "success" | "partial" | "failed" | "running" {
  if (run.inProgress > 0) return "running";
  if (run.failed === 0) return "success";
  if (run.completed === 0) return "failed";
  return "partial";
}
