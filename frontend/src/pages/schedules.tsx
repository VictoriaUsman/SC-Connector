import { useState, useMemo, useCallback } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { EmptyState } from "@/components/empty-state";
import { RunJobsList } from "@/components/run-jobs-list";
import { MultiSelectDropdown } from "@/components/multi-select-dropdown";
import { FolderConfig } from "@/components/folder-config";
import { TimeframeConfig } from "@/components/timeframe-config";
import { ReportSelector } from "@/components/report-selector";
import { useClients } from "@/hooks/use-clients";
import {
  useSchedules,
  useCreateSchedule,
  useUpdateSchedule,
  useDeleteSchedule,
  useTriggerSchedule,
} from "@/hooks/use-schedules";
import { formatDate, formatApiSource, formatReportType, formatTimeframeLabel } from "@/lib/format";
import {
  FREQUENCIES,
  DAYS_OF_WEEK,
  isAdsReportType,
} from "@/types";
import type { ApiSource, Frequency, Schedule, ScheduleConfig, Timeframe } from "@/types";
import {
  Loader2,
  MoreHorizontal,
  Plus,
  CalendarClock,
  Trash2,
  ChevronDown,
  Settings2,
  Pencil,
  Play,
  List,
  FolderOpen,
  Inbox,
  ChevronsUpDown,
  CheckCircle,
  AlertTriangle,
  XCircle,
  ExternalLink,
  Eye,
} from "lucide-react";
import { toast } from "sonner";

// ---------------------------------------------------------------------------
// Timezone conversion helper
// ---------------------------------------------------------------------------

function formatInTimezone(utcTime: string, tz: string): string {
  const [h, m] = utcTime.split(":").map(Number);
  if (Number.isNaN(h) || Number.isNaN(m)) return "--:--";
  const ref = new Date(Date.UTC(2026, 0, 15, h, m));
  return ref.toLocaleTimeString(undefined, {
    timeZone: tz,
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function TimezoneHints({ utcTime }: { utcTime: string }) {
  const pst = formatInTimezone(utcTime, "America/Los_Angeles");
  const pht = formatInTimezone(utcTime, "Asia/Manila");
  return (
    <p className="text-xs text-muted-foreground">
      {pst} PST &middot; {pht} PHT
    </p>
  );
}

// ---------------------------------------------------------------------------
// View mode persistence
// ---------------------------------------------------------------------------

type ViewMode = "list" | "grouped";
const VIEW_MODE_KEY = "schedules-view-mode";

function getPersistedViewMode(): ViewMode {
  try {
    const v = localStorage.getItem(VIEW_MODE_KEY);
    return v === "grouped" ? "grouped" : "list";
  } catch {
    return "list";
  }
}

function persistViewMode(mode: ViewMode) {
  try {
    localStorage.setItem(VIEW_MODE_KEY, mode);
  } catch { /* noop */ }
}

// ---------------------------------------------------------------------------
// Grouped schedule helpers
// ---------------------------------------------------------------------------

const DEFAULT_GROUP_KEY = "__default__";
const DEFAULT_GROUP_LABEL = "Default Layout";

interface ScheduleGroup {
  key: string;
  label: string;
  isDefault: boolean;
  schedules: Schedule[];
  activeCount: number;
}

function buildGroups(schedules: Schedule[]): ScheduleGroup[] {
  const map = new Map<string, Schedule[]>();
  for (const s of schedules) {
    const key = s.folder_name?.trim() || DEFAULT_GROUP_KEY;
    const arr = map.get(key);
    if (arr) arr.push(s);
    else map.set(key, [s]);
  }

  const groups: ScheduleGroup[] = [];
  for (const [key, items] of map) {
    groups.push({
      key,
      label: key === DEFAULT_GROUP_KEY ? DEFAULT_GROUP_LABEL : key,
      isDefault: key === DEFAULT_GROUP_KEY,
      schedules: items,
      activeCount: items.filter((s) => s.is_active).length,
    });
  }

  groups.sort((a, b) => {
    if (a.isDefault !== b.isDefault) return a.isDefault ? 1 : -1;
    return a.label.localeCompare(b.label);
  });

  return groups;
}

// ---------------------------------------------------------------------------
// Schedule Form (supports both create and edit)
// ---------------------------------------------------------------------------

interface ScheduleFormData {
  name: string;
  client_ids: string[];
  api_source: ApiSource;
  report_types: string[];
  marketplaces: string[];
  frequency: Frequency;
  schedule_config: ScheduleConfig;
  timeframe: Timeframe;
  folder_name: string;
  subfolder_strategy: "date" | "none";
  reconciliation_days: number[];
  report_params: Record<string, unknown>;
  is_active: boolean;
}

function ScheduleForm({
  clients,
  initialData,
  onSubmit,
  onCancel,
  isPending,
}: {
  clients: { id: string; name: string }[];
  initialData?: Schedule;
  onSubmit: (data: ScheduleFormData) => void;
  onCancel: () => void;
  isPending: boolean;
}) {
  const isEdit = !!initialData;

  const [name, setName] = useState(initialData?.name ?? "");
  const [clientIds, setClientIds] = useState<string[]>(initialData?.client_ids ?? []);
  const [apiSource, setApiSource] = useState<ApiSource>(initialData?.api_source ?? "sp_api");
  const [reportTypes, setReportTypes] = useState<string[]>(initialData?.report_types ?? []);
  const [reportParamsMap, setReportParamsMap] = useState<Record<string, Record<string, unknown>>>(
    (initialData?.report_params ?? {}) as Record<string, Record<string, unknown>>,
  );
  const [marketplaceIds, setMarketplaceIds] = useState<string[]>(initialData?.marketplaces ?? []);
  const [frequency, setFrequency] = useState<Frequency>(initialData?.frequency ?? "daily");
  const [scheduleTime, setScheduleTime] = useState(initialData?.schedule_config?.time ?? "03:00");
  const [daysOfWeek, setDaysOfWeek] = useState<number[]>(initialData?.schedule_config?.days_of_week ?? [0]);
  const [dayOfMonth, setDayOfMonth] = useState(initialData?.schedule_config?.day_of_month ?? 1);
  const [timeframe, setTimeframe] = useState<Timeframe>(
    initialData?.timeframe ?? { strategy: "yesterday" },
  );
  const [folderName, setFolderName] = useState(initialData?.folder_name ?? "");
  const [subfolderStrategy, setSubfolderStrategy] = useState<"date" | "none">(initialData?.subfolder_strategy ?? "date");
  const initRecon = initialData?.reconciliation_days ?? [3, 7];
  const [reconciliationEnabled, setReconciliationEnabled] = useState(initRecon.length > 0);
  const [reconDays, setReconDays] = useState<number[]>(initRecon.length > 0 ? initRecon : [3, 7]);

  const isYesterday = timeframe.strategy === "yesterday";
  const [advancedOpen, setAdvancedOpen] = useState(isEdit && initRecon.length > 0);
  const [isActive, setIsActive] = useState(initialData?.is_active ?? true);

  const clientOptions = clients.map((c) => ({ id: c.id, label: c.name }));

  const buildScheduleConfig = (): ScheduleConfig => {
    const config: ScheduleConfig = { type: frequency, time: scheduleTime };
    if (frequency === "weekly") config.days_of_week = daysOfWeek;
    if (frequency === "monthly") config.day_of_month = dayOfMonth;
    return config;
  };

  const toggleReconDay = (day: number) => {
    setReconDays((prev) =>
      prev.includes(day) ? prev.filter((d) => d !== day) : [...prev, day].sort((a, b) => a - b),
    );
  };

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit({
          name: name.trim(),
          client_ids: clientIds,
          api_source: apiSource,
          report_types: reportTypes,
          marketplaces: marketplaceIds,
          frequency,
          schedule_config: buildScheduleConfig(),
          timeframe,
          folder_name: folderName,
          subfolder_strategy: subfolderStrategy,
          reconciliation_days: isYesterday && reconciliationEnabled ? reconDays : [],
          report_params: reportParamsMap,
          is_active: isActive,
        });
      }}
      className="space-y-5 max-h-[80vh] overflow-y-auto pr-2"
    >
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label>Schedule Name</Label>
          <Input
            placeholder="e.g. Month to Date, Weekly WoW"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <MultiSelectDropdown
          label="Clients"
          options={clientOptions}
          selected={clientIds}
          onChange={setClientIds}
        />
      </div>

      <ReportSelector
        apiSource={apiSource}
        onApiSourceChange={setApiSource}
        reportTypes={reportTypes}
        onReportTypesChange={setReportTypes}
        marketplaceIds={marketplaceIds}
        onMarketplaceIdsChange={setMarketplaceIds}
        reportParamsMap={reportParamsMap}
        onReportParamsMapChange={setReportParamsMap}
      />

      {/* Frequency / Schedule Config */}
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label>Frequency</Label>
            <Select value={frequency} onValueChange={(v) => v && setFrequency(v as Frequency)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {FREQUENCIES.map((f) => (
                  <SelectItem key={f.value} value={f.value}>
                    {f.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {frequency !== "hourly" && (
            <div className="space-y-2">
              <Label>Time (UTC)</Label>
              <Input
                type="time"
                value={scheduleTime}
                onChange={(e) => setScheduleTime(e.target.value)}
              />
              <TimezoneHints utcTime={scheduleTime} />
            </div>
          )}
        </div>

        {frequency === "weekly" && (
          <div className="space-y-2">
            <Label>Days of week</Label>
            <div className="flex gap-1">
              {DAYS_OF_WEEK.map((d) => (
                <button
                  key={d.value}
                  type="button"
                  onClick={() => {
                    setDaysOfWeek((prev) =>
                      prev.includes(d.value)
                        ? prev.filter((x) => x !== d.value)
                        : [...prev, d.value].sort((a, b) => a - b),
                    );
                  }}
                  className={`px-2.5 py-1 rounded text-xs font-medium border transition-colors ${
                    daysOfWeek.includes(d.value)
                      ? "bg-primary text-primary-foreground border-primary"
                      : "bg-background border-border hover:bg-accent"
                  }`}
                >
                  {d.label}
                </button>
              ))}
            </div>
          </div>
        )}

        {frequency === "monthly" && (
          <div className="space-y-2">
            <Label>Day of month</Label>
            <Input
              type="number"
              min={1}
              max={31}
              value={dayOfMonth}
              onChange={(e) => setDayOfMonth(Number(e.target.value))}
              className="w-24"
            />
          </div>
        )}
      </div>

      <TimeframeConfig value={timeframe} onChange={setTimeframe} />

      <FolderConfig
        folderName={folderName}
        onFolderNameChange={setFolderName}
        subfolderStrategy={subfolderStrategy}
        onSubfolderStrategyChange={setSubfolderStrategy}
      />

      {/* Advanced Settings */}
      <Collapsible open={advancedOpen} onOpenChange={setAdvancedOpen}>
        <CollapsibleTrigger
          render={
            <Button type="button" variant="ghost" size="sm" className="gap-1.5 w-full justify-start text-muted-foreground">
              <Settings2 className="h-3.5 w-3.5" />
              Advanced Settings
              <ChevronDown className={`h-3.5 w-3.5 transition-transform ${advancedOpen ? "rotate-180" : ""}`} />
            </Button>
          }
        />
        <CollapsibleContent className="space-y-4 pt-2">
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <Checkbox
                checked={isYesterday && reconciliationEnabled}
                onCheckedChange={(v) => setReconciliationEnabled(!!v)}
                disabled={!isYesterday}
              />
              <Label className={`cursor-pointer ${!isYesterday ? "text-muted-foreground" : ""}`}>
                Data reconciliation (re-pull)
              </Label>
            </div>
            {!isYesterday && (
              <p className="text-xs text-muted-foreground pl-6">
                Reconciliation is only available with the &ldquo;Yesterday&rdquo; timeframe strategy
              </p>
            )}
            {isYesterday && reconciliationEnabled && (
              <div className="flex gap-2 pl-6">
                {[3, 7, 14].map((day) => (
                  <label key={day} className="flex items-center gap-1.5 text-sm cursor-pointer">
                    <Checkbox
                      checked={reconDays.includes(day)}
                      onCheckedChange={() => toggleReconDay(day)}
                    />
                    T-{day}
                  </label>
                ))}
              </div>
            )}
            {isYesterday && (
              <p className="text-xs text-muted-foreground pl-6">
                Re-pull older data to account for Amazon&apos;s delayed attribution
              </p>
            )}
          </div>
        </CollapsibleContent>
      </Collapsible>

      {isEdit && (
        <div className="flex items-center gap-3 rounded-md border p-3">
          <Switch checked={isActive} onCheckedChange={setIsActive} />
          <Label className="cursor-pointer">{isActive ? "Active" : "Paused"}</Label>
        </div>
      )}

      <DialogFooter>
        <Button type="button" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        <Button
          type="submit"
          disabled={isPending || !clientIds.length || !reportTypes.length || !marketplaceIds.length}
        >
          {isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          {isEdit ? "Save Changes" : "Create Schedule"}
        </Button>
      </DialogFooter>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Last Run Status Indicator
// ---------------------------------------------------------------------------

function RunStatusIndicator({ schedule }: { schedule: Schedule }) {
  const status = schedule.last_run_status;
  const counts = schedule.last_run_job_count;

  if (!status) return null;

  const config = {
    success: { icon: CheckCircle, className: "text-emerald-500", label: "All succeeded" },
    partial: { icon: AlertTriangle, className: "text-amber-500", label: "Partial failure" },
    failed: { icon: XCircle, className: "text-destructive", label: "All failed" },
  }[status];

  if (!config) return null;
  const Icon = config.icon;
  const tooltip = counts
    ? `${config.label} (${counts.completed}/${counts.total} completed)`
    : config.label;

  return (
    <span title={tooltip} className="inline-flex items-center">
      <Icon className={`h-3.5 w-3.5 ${config.className}`} />
    </span>
  );
}

// ---------------------------------------------------------------------------
// Schedule Table (reused in flat and grouped views)
// ---------------------------------------------------------------------------

function ScheduleTable({
  schedules,
  showFolderColumn,
  resolveClientNames,
  formatFrequencyLabel,
  onToggleActive,
  onTriggerNow,
  onEdit,
  onDelete,
  onViewLastRun,
}: {
  schedules: Schedule[];
  showFolderColumn: boolean;
  resolveClientNames: (s: Schedule) => string;
  formatFrequencyLabel: (s: Schedule) => string;
  onToggleActive: (s: Schedule) => void;
  onTriggerNow: (s: Schedule) => void;
  onEdit: (s: Schedule) => void;
  onDelete: (s: Schedule) => void;
  onViewLastRun: (s: Schedule) => void;
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Active</TableHead>
          <TableHead>Name</TableHead>
          <TableHead>Clients</TableHead>
          <TableHead>Source</TableHead>
          <TableHead>Report Types</TableHead>
          <TableHead>Marketplaces</TableHead>
          <TableHead>Schedule</TableHead>
          <TableHead>Timeframe</TableHead>
          {showFolderColumn && <TableHead>Folder</TableHead>}
          <TableHead>Last Run</TableHead>
          <TableHead>Next Run</TableHead>
          <TableHead className="w-10" />
        </TableRow>
      </TableHeader>
      <TableBody>
        {schedules.map((sched) => (
          <TableRow key={sched.id} className={sched.is_active ? "" : "opacity-50"}>
            <TableCell>
              <Switch
                checked={sched.is_active}
                onCheckedChange={() => onToggleActive(sched)}
              />
            </TableCell>
            <TableCell className="font-medium max-w-[140px] truncate" title={sched.name || "-"}>
              {sched.name || <span className="text-muted-foreground">-</span>}
            </TableCell>
            <TableCell className="font-medium max-w-[140px] truncate" title={resolveClientNames(sched)}>
              {resolveClientNames(sched)}
            </TableCell>
            <TableCell>{formatApiSource(sched.api_source)}</TableCell>
            <TableCell className="max-w-[220px]">
              <div className="flex flex-wrap gap-1">
                {sched.report_types.map((rt) => (
                  <Badge
                    key={rt}
                    variant="secondary"
                    className={`text-xs ${
                      isAdsReportType(rt)
                        ? "bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300"
                        : "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300"
                    }`}
                  >
                    {formatReportType(rt)}
                  </Badge>
                ))}
              </div>
            </TableCell>
            <TableCell>
              {(sched.marketplaces ?? []).length > 0 ? (
                <div className="flex flex-wrap gap-1">
                  {(sched.marketplaces ?? []).map((m: string) => (
                    <Badge key={m} variant="secondary" className="text-xs">
                      {m}
                    </Badge>
                  ))}
                </div>
              ) : (
                <span className="text-muted-foreground">-</span>
              )}
            </TableCell>
            <TableCell className="text-xs max-w-[160px]">
              {formatFrequencyLabel(sched)}
            </TableCell>
            <TableCell className="text-xs max-w-[140px] truncate" title={formatTimeframeLabel(sched.timeframe)}>
              {formatTimeframeLabel(sched.timeframe)}
            </TableCell>
            {showFolderColumn && (
              <TableCell className="text-xs text-muted-foreground max-w-[120px] truncate" title={sched.folder_name || "default"}>
                {sched.folder_name || "default"}
              </TableCell>
            )}
            <TableCell className="text-muted-foreground">
              {sched.last_run_at ? (
                <button
                  type="button"
                  onClick={() => onViewLastRun(sched)}
                  className="inline-flex items-center gap-1.5 hover:text-foreground hover:underline transition-colors cursor-pointer"
                  title="View last run details"
                >
                  <RunStatusIndicator schedule={sched} />
                  {formatDate(sched.last_run_at)}
                </button>
              ) : (
                <span>{formatDate(sched.last_run_at)}</span>
              )}
            </TableCell>
            <TableCell className="text-muted-foreground">
              {formatDate(sched.next_run_at)}
            </TableCell>
            <TableCell>
              <DropdownMenu>
                <DropdownMenuTrigger
                  render={
                    <Button variant="ghost" size="icon" className="h-8 w-8">
                      <MoreHorizontal className="h-4 w-4" />
                    </Button>
                  }
                />
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onClick={() => onTriggerNow(sched)}>
                    <Play className="mr-2 h-4 w-4" />
                    Run Now
                  </DropdownMenuItem>
                  {sched.last_run_at && (
                    <DropdownMenuItem onClick={() => onViewLastRun(sched)}>
                      <Eye className="mr-2 h-4 w-4" />
                      View Last Run
                    </DropdownMenuItem>
                  )}
                  {sched.last_drive_folder_id && (
                    <DropdownMenuItem
                      onClick={() =>
                        window.open(
                          `https://drive.google.com/drive/folders/${sched.last_drive_folder_id}`,
                          "_blank",
                        )
                      }
                    >
                      <ExternalLink className="mr-2 h-4 w-4" />
                      Open Drive Folder
                    </DropdownMenuItem>
                  )}
                  <DropdownMenuItem onClick={() => onEdit(sched)}>
                    <Pencil className="mr-2 h-4 w-4" />
                    Edit
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    variant="destructive"
                    onClick={() => onDelete(sched)}
                  >
                    <Trash2 className="mr-2 h-4 w-4" />
                    Delete
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

// ---------------------------------------------------------------------------
// Schedules Page
// ---------------------------------------------------------------------------

export function Schedules() {
  const { data: clients, isLoading: clientsLoading } = useClients();
  const [filterClient, setFilterClient] = useState<string>("");
  const { data: schedules, isLoading } = useSchedules(filterClient || undefined);
  const createSchedule = useCreateSchedule();
  const updateSchedule = useUpdateSchedule();
  const deleteSchedule = useDeleteSchedule();
  const triggerSchedule = useTriggerSchedule();
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<Schedule | null>(null);
  const [viewRunTarget, setViewRunTarget] = useState<Schedule | null>(null);

  const [viewMode, setViewMode] = useState<ViewMode>(getPersistedViewMode);
  const [openGroups, setOpenGroups] = useState<string[]>([]);

  const clientMap = new Map((clients ?? []).map((c) => [c.id, c.name]));
  const activeClients = (clients ?? []).filter((c) => c.is_active);

  const sortedSchedules = useMemo(
    () => [...(schedules ?? [])].sort((a, b) =>
      (b.created_at ?? "").localeCompare(a.created_at ?? ""),
    ),
    [schedules],
  );

  const groups = useMemo(() => buildGroups(sortedSchedules), [sortedSchedules]);

  const allGroupKeys = useMemo(() => groups.map((g) => g.key), [groups]);
  const allExpanded = openGroups.length === allGroupKeys.length;

  const toggleViewMode = useCallback((mode: ViewMode) => {
    setViewMode(mode);
    persistViewMode(mode);
    if (mode === "grouped") {
      setOpenGroups(allGroupKeys);
    }
  }, [allGroupKeys]);

  const toggleExpandAll = useCallback(() => {
    setOpenGroups(allExpanded ? [] : allGroupKeys);
  }, [allExpanded, allGroupKeys]);

  const resolveClientNames = (sched: Schedule) => {
    const ids = sched.client_ids ?? [];
    if (!ids.length) return "-";
    return ids.map((id) => clientMap.get(id) ?? id).join(", ");
  };

  const handleToggleActive = (schedule: Schedule) => {
    updateSchedule.mutate(
      { id: schedule.id, is_active: !schedule.is_active },
      {
        onSuccess: () =>
          toast.success(`Schedule ${schedule.is_active ? "paused" : "activated"}`),
        onError: (err) => toast.error(err.message),
      },
    );
  };

  const handleDelete = (schedule: Schedule) => {
    if (!confirm("Delete this schedule? This cannot be undone.")) return;
    deleteSchedule.mutate(schedule.id, {
      onSuccess: () => toast.success("Schedule deleted"),
      onError: (err) => toast.error(err.message),
    });
  };

  const computeJobCount = (schedule: Schedule) => {
    const mktCount = (schedule.marketplaces ?? []).length;
    const clientCount = (schedule.client_ids ?? []).length;
    const reportCount = schedule.report_types.length || 1;
    const reconCount = (schedule.reconciliation_days ?? []).length + 1;
    return clientCount * mktCount * reportCount * reconCount;
  };

  const handleTriggerNow = (schedule: Schedule) => {
    const totalJobs = computeJobCount(schedule);
    if (!confirm(`Run now? This will launch ${totalJobs} job${totalJobs > 1 ? "s" : ""} immediately.`)) return;

    triggerSchedule.mutate(schedule.id, {
      onSuccess: (result) => {
        toast.success(`Triggered ${result.jobs_started} job${result.jobs_started !== 1 ? "s" : ""}`, {
          description: "Check the Dashboard for progress",
        });
      },
      onError: (err) => toast.error(err.message),
    });
  };

  const handleTriggerGroup = (group: ScheduleGroup) => {
    const activeSchedules = group.schedules.filter((s) => s.is_active);
    if (!activeSchedules.length) {
      toast.error("No active schedules in this folder");
      return;
    }
    const totalJobs = activeSchedules.reduce((sum, s) => sum + computeJobCount(s), 0);
    if (!confirm(
      `Run all ${activeSchedules.length} active schedule${activeSchedules.length > 1 ? "s" : ""} in "${group.label}"?\n\nThis will launch ${totalJobs} job${totalJobs > 1 ? "s" : ""} immediately.`
    )) return;

    let started = 0;
    let failed = 0;
    for (const sched of activeSchedules) {
      triggerSchedule.mutate(sched.id, {
        onSuccess: (result) => {
          started += result.jobs_started;
          if (started + failed === activeSchedules.length) {
            toast.success(`Triggered ${started} job${started !== 1 ? "s" : ""} across ${activeSchedules.length} schedule${activeSchedules.length > 1 ? "s" : ""}`, {
              description: "Check the Dashboard for progress",
            });
          }
        },
        onError: () => {
          failed++;
          if (started + failed === activeSchedules.length) {
            toast.error(`${failed} schedule${failed > 1 ? "s" : ""} failed to trigger`);
          }
        },
      });
    }
  };

  const handleEdit = (schedule: Schedule, data: ScheduleFormData) => {
    updateSchedule.mutate(
      { id: schedule.id, ...data },
      {
        onSuccess: () => {
          setEditTarget(null);
          toast.success("Schedule updated");
        },
        onError: (err) => toast.error(err.message),
      },
    );
  };

  const formatFrequencyLabel = (sched: Schedule) => {
    const config = sched.schedule_config;
    if (!config) return sched.frequency;
    const time = config.time ?? "";
    if (config.type === "weekly" && config.days_of_week?.length) {
      const dayNames = config.days_of_week.map(
        (d) => DAYS_OF_WEEK.find((dw) => dw.value === d)?.label ?? d,
      );
      return `Weekly ${dayNames.join(", ")} ${time}`;
    }
    if (config.type === "monthly" && config.day_of_month) {
      return `Monthly on the ${config.day_of_month}${ordinalSuffix(config.day_of_month)} ${time}`;
    }
    if (config.type === "daily") return `Daily ${time}`;
    return config.type;
  };

  if (isLoading || clientsLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">Schedules</h1>
        <Dialog open={createOpen} onOpenChange={setCreateOpen}>
          <Button onClick={() => setCreateOpen(true)}>
            <Plus className="mr-2 h-4 w-4" />
            New Schedule
          </Button>
          <DialogContent className="sm:max-w-2xl">
            <DialogHeader>
              <DialogTitle>Create Schedule</DialogTitle>
            </DialogHeader>
            <ScheduleForm
              clients={activeClients}
              onSubmit={(data) => {
                createSchedule.mutate(data, {
                  onSuccess: () => {
                    setCreateOpen(false);
                    toast.success("Schedule created");
                  },
                  onError: (err) => toast.error(err.message),
                });
              }}
              onCancel={() => setCreateOpen(false)}
              isPending={createSchedule.isPending}
            />
          </DialogContent>
        </Dialog>
      </div>

      {/* Edit Dialog */}
      <Dialog open={!!editTarget} onOpenChange={(open) => { if (!open) setEditTarget(null); }}>
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>Edit Schedule</DialogTitle>
          </DialogHeader>
          {editTarget && (
            <ScheduleForm
              key={editTarget.id}
              clients={activeClients}
              initialData={editTarget}
              onSubmit={(data) => handleEdit(editTarget, data)}
              onCancel={() => setEditTarget(null)}
              isPending={updateSchedule.isPending}
            />
          )}
        </DialogContent>
      </Dialog>

      {/* View Last Run Dialog */}
      <Dialog open={!!viewRunTarget} onOpenChange={(open) => { if (!open) setViewRunTarget(null); }}>
        <DialogContent className="sm:max-w-3xl max-h-[80vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>
              Last Run {viewRunTarget?.name ? `\u2014 ${viewRunTarget.name}` : ""}
            </DialogTitle>
          </DialogHeader>
          {viewRunTarget && (
            <RunJobsList scheduleId={viewRunTarget.id} />
          )}
        </DialogContent>
      </Dialog>

      {/* Toolbar: filter + view toggle */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Label className="text-sm text-muted-foreground">Filter by client:</Label>
          <Select value={filterClient} onValueChange={(v) => setFilterClient(v === "all" ? "" : (v ?? ""))}>
            <SelectTrigger className="w-[200px]">
              <SelectValue placeholder="All clients" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All clients</SelectItem>
              {(clients ?? []).map((c) => (
                <SelectItem key={c.id} value={c.id}>
                  {c.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="flex items-center gap-2">
          {viewMode === "grouped" && (schedules?.length ?? 0) > 0 && (
            <Button
              variant="ghost"
              size="sm"
              onClick={toggleExpandAll}
              className="text-xs text-muted-foreground gap-1.5"
            >
              <ChevronsUpDown className="h-3.5 w-3.5" />
              {allExpanded ? "Collapse All" : "Expand All"}
            </Button>
          )}
          <div className="flex items-center rounded-lg border bg-muted p-0.5">
            <button
              onClick={() => toggleViewMode("list")}
              className={`flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
                viewMode === "list"
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              <List className="h-3.5 w-3.5" />
              List
            </button>
            <button
              onClick={() => toggleViewMode("grouped")}
              className={`flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
                viewMode === "grouped"
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              <FolderOpen className="h-3.5 w-3.5" />
              Grouped
            </button>
          </div>
        </div>
      </div>

      {/* Content */}
      {!sortedSchedules.length ? (
        <Card>
          <CardContent className="pt-6">
            <EmptyState
              icon={CalendarClock}
              title="No schedules"
              description="Create a schedule to automatically generate reports on a recurring basis."
              action={
                <Button variant="outline" onClick={() => setCreateOpen(true)}>
                  <Plus className="mr-2 h-4 w-4" />
                  New Schedule
                </Button>
              }
            />
          </CardContent>
        </Card>
      ) : viewMode === "list" ? (
        <Card>
          <CardHeader>
            <CardTitle>Report Schedules</CardTitle>
          </CardHeader>
          <CardContent>
            <ScheduleTable
              schedules={sortedSchedules}
              showFolderColumn
              resolveClientNames={resolveClientNames}
              formatFrequencyLabel={formatFrequencyLabel}
              onToggleActive={handleToggleActive}
              onTriggerNow={handleTriggerNow}
              onEdit={setEditTarget}
              onDelete={handleDelete}
              onViewLastRun={setViewRunTarget}
            />
          </CardContent>
        </Card>
      ) : (
        <Accordion
          multiple
          value={openGroups}
          onValueChange={setOpenGroups}
          className="space-y-3"
        >
          {groups.map((group) => (
            <AccordionItem
              key={group.key}
              value={group.key}
              className="rounded-lg border bg-card shadow-sm not-last:border-b"
            >
              <AccordionTrigger className="px-4 py-3 hover:no-underline">
                <div className="flex flex-1 items-center gap-3 mr-2">
                  {group.isDefault ? (
                    <Inbox className="h-4 w-4 text-muted-foreground shrink-0" />
                  ) : (
                    <FolderOpen className="h-4 w-4 text-muted-foreground shrink-0" />
                  )}
                  <span className="font-semibold text-sm">{group.label}</span>
                  <Badge variant="secondary" className="text-xs tabular-nums">
                    {group.schedules.length} schedule{group.schedules.length !== 1 ? "s" : ""}
                  </Badge>
                  <span className="text-xs text-muted-foreground">
                    {group.activeCount} active
                  </span>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-xs gap-1.5 mr-2 shrink-0"
                  onClick={(e) => {
                    e.stopPropagation();
                    handleTriggerGroup(group);
                  }}
                >
                  <Play className="h-3 w-3" />
                  Run All
                </Button>
              </AccordionTrigger>
              <AccordionContent className="px-4 pb-4">
                <ScheduleTable
                  schedules={group.schedules}
                  showFolderColumn={false}
                  resolveClientNames={resolveClientNames}
                  formatFrequencyLabel={formatFrequencyLabel}
                  onToggleActive={handleToggleActive}
                  onTriggerNow={handleTriggerNow}
                  onEdit={setEditTarget}
                  onDelete={handleDelete}
                  onViewLastRun={setViewRunTarget}
                />
              </AccordionContent>
            </AccordionItem>
          ))}
        </Accordion>
      )}
    </div>
  );
}

function ordinalSuffix(n: number) {
  const s = ["th", "st", "nd", "rd"];
  const v = n % 100;
  return s[(v - 20) % 10] || s[v] || s[0];
}
