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
 */
export function useRealtimeJobs(opts?: {
  clientId?: string;
  status?: string;
  max?: number;
}) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    const constraints: QueryConstraint[] = [];
    if (opts?.clientId) constraints.push(where("client_id", "==", opts.clientId));
    if (opts?.status) constraints.push(where("status", "==", opts.status));
    constraints.push(orderBy("started_at", "desc"));
    constraints.push(limit(opts?.max ?? 100));

    const q = query(collection(db, "jobs"), ...constraints);

    const unsub = onSnapshot(
      q,
      (snap) => {
        setJobs(
          snap.docs.map((doc) => docToJob(doc.id, doc.data())),
        );
        setLoading(false);
      },
      (err) => {
        setError(err);
        setLoading(false);
      },
    );

    return unsub;
  }, [opts?.clientId, opts?.status, opts?.max]);

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
