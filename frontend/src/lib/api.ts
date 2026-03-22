import type { Client, Schedule, Job, AdsReportConfigMap } from "@/types";

const BASE_URL = import.meta.env.VITE_API_URL;

class ApiError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await res.json();
  if (!res.ok) {
    throw new ApiError(res.status, data.code ?? "UNKNOWN", data.error ?? res.statusText);
  }
  return data as T;
}

export const api = {
  // Clients
  listClients: (active?: boolean) =>
    request<Client[]>(`/clients${active ? "?active=true" : ""}`),
  getClient: (id: string) => request<Client>(`/clients/${id}`),
  createClient: (data: { id: string; name: string; marketplaces: string[] }) =>
    request<{ id: string; status: string }>("/clients", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  updateClient: (id: string, data: Partial<Client>) =>
    request<{ id: string; status: string }>(`/clients/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  deleteClient: (id: string) =>
    request<{ id: string; status: string }>(`/clients/${id}`, { method: "DELETE" }),

  // Schedules
  listSchedules: (params?: { client_id?: string; active?: boolean }) => {
    const sp = new URLSearchParams();
    if (params?.client_id) sp.set("client_id", params.client_id);
    if (params?.active) sp.set("active", "true");
    const qs = sp.toString();
    return request<Schedule[]>(`/schedules${qs ? `?${qs}` : ""}`);
  },
  createSchedule: (data: Omit<Schedule, "id" | "created_at" | "updated_at" | "last_run_at" | "next_run_at">) =>
    request<{ id: string; status: string }>("/schedules", {
      method: "POST",
      body: JSON.stringify(data),
    }) as Promise<{ id: string; status: string }>,
  updateSchedule: (id: string, data: Partial<Schedule>) =>
    request<{ id: string; status: string }>(`/schedules/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  deleteSchedule: (id: string) =>
    request<{ id: string; status: string }>(`/schedules/${id}`, { method: "DELETE" }),
  triggerSchedule: (id: string) =>
    request<{ schedule_id: string; status: string; jobs_started: number; job_ids: string[]; errors: number }>(
      `/schedules/${id}/trigger`,
      { method: "POST" },
    ),

  // Jobs
  listJobs: (params?: { client_id?: string; status?: string; limit?: number }) => {
    const sp = new URLSearchParams();
    if (params?.client_id) sp.set("client_id", params.client_id);
    if (params?.status) sp.set("status", params.status);
    if (params?.limit) sp.set("limit", String(params.limit));
    const qs = sp.toString();
    return request<Job[]>(`/jobs${qs ? `?${qs}` : ""}`);
  },
  getJob: (id: string) => request<Job>(`/jobs/${id}`),

  // OAuth
  getOAuthStatus: (clientId: string) =>
    request<{ client_id: string; sp_api_connected: boolean; ads_api_connected: boolean }>(
      `/oauth/status/${clientId}`,
    ),
  getSpApiAuthUrl: (clientId: string) => `${BASE_URL}/oauth/sp-api/authorize?client_id=${clientId}`,
  getAdsApiAuthUrl: (clientId: string) => `${BASE_URL}/oauth/ads-api/authorize?client_id=${clientId}`,
  connectManual: (clientId: string, data: { api_source: string; refresh_token?: string; profile_id?: string }) =>
    request<{ id: string; api_source: string; status: string }>(`/clients/${clientId}/connect`, {
      method: "POST",
      body: JSON.stringify(data),
    }),

  // Ads report config
  getAdsReportConfig: () => request<AdsReportConfigMap>("/ads-report-config"),

  // On-demand
  triggerOnDemand: (data: {
    client_id: string;
    api_source: string;
    marketplace: string;
    report_types: string[];
    report_params?: Record<string, unknown>;
    start_date?: string;
    end_date?: string;
    folder_name?: string;
    subfolder_strategy?: "date" | "none";
  }) =>
    request<{ job_ids: string[]; jobs_started: number; errors: number; status: string }>("/on-demand", {
      method: "POST",
      body: JSON.stringify(data),
    }),
};
