import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { BotConfig } from "@/types";

export function useBotConfigs() {
  return useQuery({ queryKey: ["bot-configs"], queryFn: () => api.listBotConfigs() });
}

export function useUpsertBotConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ clientId, ...data }: Partial<BotConfig> & { clientId: string }) =>
      api.upsertBotConfig(clientId, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["bot-configs"] }),
  });
}
