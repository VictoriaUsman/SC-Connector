import { useState } from "react";
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
import { EmptyState } from "@/components/empty-state";
import { MultiSelectDropdown } from "@/components/multi-select-dropdown";
import { FolderConfig } from "@/components/folder-config";
import { TimeframeConfig } from "@/components/timeframe-config";
import { ReportSelector } from "@/components/report-selector";
import type { AdsReportParams } from "@/components/ads-report-config";
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
} from "lucide-react";
import { toast } from "sonner";

// ---------------------------------------------------------------------------
// Schedule Form (supports both create and edit)
// ---------------------------------------------------------------------------

interface ScheduleFormData {
  client_ids: string[];
  api_source: ApiSource;
  report_type: string;
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

  const [clientIds, setClientIds] = useState<string[]>(initialData?.client_ids ?? []);
  const [apiSource, setApiSource] = useState<ApiSource>(initialData?.api_source ?? "sp_api");
  const [reportType, setReportType] = useState(initialData?.report_type ?? "");
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
  const [adsConfig, setAdsConfig] = useState<AdsReportParams>(() => {
    if (!initialData?.report_params) return {};
    const p = initialData.report_params;
    return {
      columns: p.columns as string[] | undefined,
      timeUnit: p.timeUnit as string | undefined,
    };
  });
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
        const reportParams: Record<string, unknown> = {};
        if (apiSource === "ads_api") {
          if (adsConfig.columns) reportParams.columns = adsConfig.columns;
          if (adsConfig.timeUnit) reportParams.timeUnit = adsConfig.timeUnit;
        }
        onSubmit({
          client_ids: clientIds,
          api_source: apiSource,
          report_type: reportType,
          marketplaces: marketplaceIds,
          frequency,
          schedule_config: buildScheduleConfig(),
          timeframe,
          folder_name: folderName,
          subfolder_strategy: subfolderStrategy,
          reconciliation_days: isYesterday && reconciliationEnabled ? reconDays : [],
          report_params: reportParams,
          is_active: isActive,
        });
      }}
      className="space-y-4 max-h-[70vh] overflow-y-auto pr-1"
    >
      <MultiSelectDropdown
        label="Clients"
        options={clientOptions}
        selected={clientIds}
        onChange={setClientIds}
      />

      <ReportSelector
        apiSource={apiSource}
        onApiSourceChange={setApiSource}
        reportType={reportType}
        onReportTypeChange={setReportType}
        marketplaceIds={marketplaceIds}
        onMarketplaceIdsChange={setMarketplaceIds}
        adsConfig={adsConfig}
        onAdsConfigChange={setAdsConfig}
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
          disabled={isPending || !clientIds.length || !reportType || !marketplaceIds.length}
        >
          {isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          {isEdit ? "Save Changes" : "Create Schedule"}
        </Button>
      </DialogFooter>
    </form>
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

  const clientMap = new Map((clients ?? []).map((c) => [c.id, c.name]));
  const activeClients = (clients ?? []).filter((c) => c.is_active);

  const resolveClientNames = (sched: Schedule) => {
    const ids = sched.client_ids ?? [];
    if (!ids.length) return "-";
    return ids.map((id) => clientMap.get(id) ?? id).join(", ");
  };

  const resolveMarketplaces = (sched: Schedule): string[] => {
    return sched.marketplaces ?? [];
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

  const handleTriggerNow = (schedule: Schedule) => {
    const mktCount = (schedule.marketplaces ?? []).length;
    const clientCount = (schedule.client_ids ?? []).length;
    const reconCount = (schedule.reconciliation_days ?? []).length + 1;
    const totalJobs = clientCount * mktCount * reconCount;

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
          <DialogContent className="sm:max-w-lg">
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
        <DialogContent className="sm:max-w-lg">
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

      <Card>
        <CardHeader>
          <CardTitle>Report Schedules</CardTitle>
        </CardHeader>
        <CardContent>
          {!schedules?.length ? (
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
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Active</TableHead>
                  <TableHead>Clients</TableHead>
                  <TableHead>Source</TableHead>
                  <TableHead>Report Type</TableHead>
                  <TableHead>Marketplaces</TableHead>
                  <TableHead>Schedule</TableHead>
                  <TableHead>Timeframe</TableHead>
                  <TableHead>Folder</TableHead>
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
                        onCheckedChange={() => handleToggleActive(sched)}
                      />
                    </TableCell>
                    <TableCell className="font-medium max-w-[140px] truncate" title={resolveClientNames(sched)}>
                      {resolveClientNames(sched)}
                    </TableCell>
                    <TableCell>{formatApiSource(sched.api_source)}</TableCell>
                    <TableCell className="max-w-[180px] truncate" title={sched.report_type}>
                      {formatReportType(sched.report_type)}
                    </TableCell>
                    <TableCell>
                      {resolveMarketplaces(sched).length > 0 ? (
                        <div className="flex flex-wrap gap-1">
                          {resolveMarketplaces(sched).map((m: string) => (
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
                    <TableCell className="text-xs text-muted-foreground max-w-[120px] truncate" title={sched.folder_name || "default"}>
                      {sched.folder_name || "default"}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {formatDate(sched.last_run_at)}
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
                          <DropdownMenuItem onClick={() => handleTriggerNow(sched)}>
                            <Play className="mr-2 h-4 w-4" />
                            Run Now
                          </DropdownMenuItem>
                          <DropdownMenuItem onClick={() => setEditTarget(sched)}>
                            <Pencil className="mr-2 h-4 w-4" />
                            Edit
                          </DropdownMenuItem>
                          <DropdownMenuSeparator />
                          <DropdownMenuItem
                            variant="destructive"
                            onClick={() => handleDelete(sched)}
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
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function ordinalSuffix(n: number) {
  const s = ["th", "st", "nd", "rd"];
  const v = n % 100;
  return s[(v - 20) % 10] || s[v] || s[0];
}
