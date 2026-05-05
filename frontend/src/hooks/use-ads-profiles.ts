import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export function useAdsProfiles() {
  return useQuery({
    queryKey: ["ads-profiles"],
    queryFn: () => api.listAdsProfiles(),
    staleTime: 60_000,
    retry: 1,
  });
}
