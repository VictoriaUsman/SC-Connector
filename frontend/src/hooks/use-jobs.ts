import { useEffect, useState } from "react";
import {
  collection,
  query,
  orderBy,
  limit,
  where,
  onSnapshot,
  Timestamp,
  type QueryConstraint,
  type DocumentData,
} from "firebase/firestore";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { db } from "@/lib/firebase";
import { api } from "@/lib/api";
import type { Job } from "@/types";

function firestoreTimestampToISO(val: unknown): string | undefined {
  if (val instanceof Timestamp) return val.toDate().toISOString();
  if (typeof val === "string") return val;
  return undefined;
}

function docToJob(id: string, data: DocumentData): Job {
  return {
    ...data,
    id,
    started_at: firestoreTimestampToISO(data.started_at),
    completed_at: firestoreTimestampToISO(data.completed_at),
  } as Job;
}

/**
 * Real-time Firestore listener for the jobs collection.
 * Updates automatically when job statuses change in the backend.
 * Pass `undefined` to disable the listener (no Firestore query runs).
 */
export function useRealtimeJobs(opts?: {
  clientId?: string;
  scheduleId?: string;
  status?: string;
  max?: number;
} | undefined) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(!!opts);
  const [error, setError] = useState<Error | null>(null);

  const enabled = opts !== undefined;

  useEffect(() => {
    if (!enabled) {
      setJobs([]);
      setLoading(false);
      return;
    }

    setLoading(true);
    const constraints: QueryConstraint[] = [];
    if (opts?.scheduleId) constraints.push(where("schedule_id", "==", opts.scheduleId));
    if (opts?.clientId) constraints.push(where("client_id", "==", opts.clientId));
    if (opts?.status) constraints.push(where("status", "==", opts.status));
    constraints.push(orderBy("started_at", "desc"));
    constraints.push(limit(opts?.max ?? 100));

    const q = query(collection(db, "jobs"), ...constraints);

    const unsub = onSnapshot(
      q,
      (snap) => {
        setJobs(snap.docs.map((doc) => docToJob(doc.id, doc.data())));
        setLoading(false);
      },
      (err) => {
        setError(err);
        setLoading(false);
      },
    );

    return unsub;
  }, [enabled, opts?.clientId, opts?.scheduleId, opts?.status, opts?.max]);

  return { jobs, loading, error };
}

/**
 * Real-time listener for jobs belonging to a specific schedule run
 * (identified by schedule_id + execution_date).
 */
export function useRunJobs(scheduleId: string | undefined, executionDate: string | undefined) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    if (!scheduleId || !executionDate) {
      setJobs([]);
      setLoading(false);
      return;
    }

    const q = query(
      collection(db, "jobs"),
      where("schedule_id", "==", scheduleId),
      where("execution_date", "==", executionDate),
      orderBy("started_at", "desc"),
    );

    const unsub = onSnapshot(
      q,
      (snap) => {
        setJobs(snap.docs.map((doc) => docToJob(doc.id, doc.data())));
        setLoading(false);
      },
      (err) => {
        setError(err);
        setLoading(false);
      },
    );

    return unsub;
  }, [scheduleId, executionDate]);

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
