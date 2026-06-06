import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { AccountType, Client } from "@/types";

export function useClients() {
  return useQuery({ queryKey: ["clients"], queryFn: () => api.listClients() });
}

export function useCreateClient() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { id: string; name: string; marketplaces: string[]; account_type?: AccountType }) =>
      api.createClient(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["clients"] }),
  });
}

export function useUpdateClient() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...data }: Partial<Client> & { id: string }) =>
      api.updateClient(id, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["clients"] }),
  });
}

export function useDeleteClient() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteClient(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["clients"] }),
  });
}
