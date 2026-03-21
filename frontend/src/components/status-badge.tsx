import { Badge } from "@/components/ui/badge";
import type { JobStatus } from "@/types";

const STATUS_CONFIG: Record<JobStatus, { variant: "default" | "secondary" | "destructive" | "outline"; label: string }> = {
  pending: { variant: "secondary", label: "Pending" },
  requesting: { variant: "outline", label: "Requesting" },
  polling: { variant: "outline", label: "Polling" },
  downloading: { variant: "outline", label: "Downloading" },
  uploading: { variant: "outline", label: "Uploading" },
  completed: { variant: "default", label: "Completed" },
  failed: { variant: "destructive", label: "Failed" },
};

export function StatusBadge({ status }: { status: JobStatus }) {
  const config = STATUS_CONFIG[status] ?? { variant: "secondary" as const, label: status };
  return <Badge variant={config.variant}>{config.label}</Badge>;
}
