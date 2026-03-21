import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Schedule } from "@/types";

export function useSchedules(clientId?: string) {
  return useQuery({
    queryKey: ["schedules", clientId],
    queryFn: () => api.listSchedules(clientId ? { client_id: clientId } : undefined),
  });
}

type CreateScheduleData = Omit<Schedule, "id" | "created_at" | "updated_at" | "last_run_at" | "next_run_at">;

export function useCreateSchedule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: CreateScheduleData) => api.createSchedule(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["schedules"] }),
  });
}

export function useUpdateSchedule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...data }: Partial<Schedule> & { id: string }) =>
      api.updateSchedule(id, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["schedules"] }),
  });
}

export function useDeleteSchedule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteSchedule(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["schedules"] }),
  });
}

export function useTriggerSchedule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.triggerSchedule(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["schedules"] });
      qc.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}
