import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export function useSpApiAccounts() {
  return useQuery({
    queryKey: ["sp-api-accounts"],
    queryFn: () => api.listSpApiAccounts(),
    staleTime: 60_000,
    retry: 1,
  });
}
