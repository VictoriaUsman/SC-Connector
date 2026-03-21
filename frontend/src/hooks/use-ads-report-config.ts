import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export function useAdsReportConfig() {
  return useQuery({
    queryKey: ["ads-report-config"],
    queryFn: () => api.getAdsReportConfig(),
    staleTime: 1000 * 60 * 30,
  });
}
