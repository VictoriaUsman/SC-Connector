import { useEffect, useId, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { supabase } from "@/lib/supabase";
import { api } from "@/lib/api";
import type { Job } from "@/types";

type JobsFilter = {
  clientId?: string;
  scheduleId?: string;
  status?: string;
  max?: number;
};

async function fetchJobs(filter: JobsFilter): Promise<Job[]> {
  let query = supabase.from("jobs").select("*").order("started_at", { ascending: false });
  if (filter.clientId) query = query.eq("client_id", filter.clientId);
  if (filter.scheduleId) query = query.eq("schedule_id", filter.scheduleId);
  if (filter.status) query = query.eq("status", filter.status);
  query = query.limit(filter.max ?? 100);

  const { data, error } = await query;
  if (error) throw error;
  return (data ?? []) as Job[];
}

/**
 * Real-time Supabase listener for the jobs table.
 * Updates automatically when job statuses change in the backend.
 * Pass `undefined` to disable the listener (no query or subscription runs).
 */
export function useRealtimeJobs(opts?: JobsFilter | undefined) {
  const instanceId = useId();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(!!opts);
  const [error, setError] = useState<Error | null>(null);

  const enabled = opts !== undefined;
  const clientId = opts?.clientId;
  const scheduleId = opts?.scheduleId;
  const status = opts?.status;
  const max = opts?.max;

  useEffect(() => {
    if (!enabled) {
      setJobs([]);
      setLoading(false);
      return;
    }

    let cancelled = false;

    async function load(showLoading: boolean) {
      if (showLoading) setLoading(true);
      try {
        const data = await fetchJobs({ clientId, scheduleId, status, max });
        if (!cancelled) {
          setJobs(data);
          setLoading(false);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err as Error);
          setLoading(false);
        }
      }
    }

    load(true);

    // Realtime's postgres_changes filter only supports one column condition
    // per subscription, but callers here combine up to three (clientId,
    // scheduleId, status). Subscribe unfiltered to every change on the
    // table and re-run the fully-filtered `load()` query on each event
    // instead — correct for any combination of filters, at the cost of an
    // extra refetch when an unrelated job row changes (acceptable at this
    // table's scale). `showLoading` is false on these refetches so a
    // realtime event doesn't flash the whole list back to a loading state.
    //
    // The channel topic uses `useId()` alone (not the filter values) so
    // each mounted hook instance gets its own channel — Supabase's
    // realtime-js reuses a channel by topic when one already exists, so
    // two instances sharing a topic would share a channel and one
    // unmounting would silently kill the other's subscription.
    const channel = supabase
      .channel(`jobs-realtime-${instanceId}`)
      .on("postgres_changes", { event: "*", schema: "public", table: "jobs" }, () => {
        load(false);
      })
      .subscribe((subStatus, err) => {
        if (err) {
          console.warn("[useRealtimeJobs] realtime subscription error:", err);
        } else if (subStatus !== "SUBSCRIBED" && subStatus !== "CLOSED") {
          console.warn(`[useRealtimeJobs] realtime subscription status: ${subStatus}`);
        }
      });

    return () => {
      cancelled = true;
      supabase.removeChannel(channel);
    };
  }, [enabled, clientId, scheduleId, status, max, instanceId]);

  return { jobs, loading, error };
}

/**
 * Real-time listener for jobs belonging to a specific schedule run
 * (identified by schedule_id + execution_date).
 */
export function useRunJobs(scheduleId: string | undefined, executionDate: string | undefined) {
  const instanceId = useId();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    if (!scheduleId || !executionDate) {
      setJobs([]);
      setLoading(false);
      return;
    }

    let cancelled = false;

    async function load(showLoading: boolean) {
      if (showLoading) setLoading(true);
      const { data, error: err } = await supabase
        .from("jobs")
        .select("*")
        .eq("schedule_id", scheduleId)
        .eq("execution_date", executionDate)
        .order("started_at", { ascending: false });

      if (cancelled) return;
      if (err) {
        setError(err as unknown as Error);
      } else {
        setJobs((data ?? []) as Job[]);
      }
      setLoading(false);
    }

    load(true);

    const channel = supabase
      .channel(`run-jobs-realtime-${instanceId}`)
      .on("postgres_changes", { event: "*", schema: "public", table: "jobs" }, () => {
        load(false);
      })
      .subscribe((subStatus, err) => {
        if (err) {
          console.warn("[useRunJobs] realtime subscription error:", err);
        } else if (subStatus !== "SUBSCRIBED" && subStatus !== "CLOSED") {
          console.warn(`[useRunJobs] realtime subscription status: ${subStatus}`);
        }
      });

    return () => {
      cancelled = true;
      supabase.removeChannel(channel);
    };
  }, [scheduleId, executionDate, instanceId]);

  return { jobs, loading, error };
}

export function useTriggerOnDemand() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: {
      client_id: string;
      api_source: string;
      marketplace: string;
      report_types: string[];
      report_params?: Record<string, unknown>;
      start_date?: string;
      end_date?: string;
      folder_name?: string;
      subfolder_strategy?: "date" | "none";
    }) => api.triggerOnDemand(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useRetryJob() {
  return useMutation({
    mutationFn: (jobId: string) => api.retryJob(jobId),
  });
}

export function useBatchRetryJobs() {
  return useMutation({
    mutationFn: (jobIds: string[]) => api.batchRetryJobs(jobIds),
  });
}
