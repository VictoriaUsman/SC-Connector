import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Event } from "@/types";

export function useEvents() {
  return useQuery({ queryKey: ["events"], queryFn: () => api.listEvents() });
}

export function useCreateEvent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { name: string; start_date: string; end_date: string }) =>
      api.createEvent(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["events"] }),
  });
}

export function useUpdateEvent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...data }: Partial<Event> & { id: string }) =>
      api.updateEvent(id, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["events"] }),
  });
}

export function useDeleteEvent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteEvent(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["events"] }),
  });
}

export function useActivateEvent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.activateEvent(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["events"] }),
  });
}

export function useDeactivateEvent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deactivateEvent(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["events"] }),
  });
}
