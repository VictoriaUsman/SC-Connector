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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
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
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { EmptyState } from "@/components/empty-state";
import { useClients } from "@/hooks/use-clients";
import {
  useEvents,
  useCreateEvent,
  useUpdateEvent,
  useDeleteEvent,
  useActivateEvent,
  useDeactivateEvent,
} from "@/hooks/use-events";
import { useBotConfigs, useUpsertBotConfig } from "@/hooks/use-bot-configs";
import {
  MARKETPLACES,
  CURRENCIES,
  CLIENT_TIMEZONES,
  SLACK_USERS,
} from "@/types";
import { MultiSelectDropdown } from "@/components/multi-select-dropdown";
import type { Event, BotConfig, Client, EventStatus, ManualAds, SlackChannel } from "@/types";
import {
  Loader2,
  MoreHorizontal,
  Plus,
  Pencil,
  Trash2,
  Play,
  Square,
  Zap,
  Calendar,
  MessageSquare,
} from "lucide-react";
import { toast } from "sonner";

// ---------------------------------------------------------------------------
// Bot config channels — with legacy single-channel fallback
// ---------------------------------------------------------------------------

/**
 * The channels a bot config broadcasts to. Configs saved before
 * multi-channel support only have slack_channel_id/slack_channel_name, so
 * those are read as a one-item list when `channels` is absent.
 */
function getConfigChannels(config?: BotConfig): SlackChannel[] {
  if (!config) return [];
  if (config.channels?.length) return config.channels;
  if (config.slack_channel_id) {
    return [{ id: config.slack_channel_id, name: config.slack_channel_name }];
  }
  return [];
}

// ---------------------------------------------------------------------------
// Event status badge
// ---------------------------------------------------------------------------

function EventStatusBadge({ status }: { status: EventStatus }) {
  const variants: Record<EventStatus, { className: string; label: string }> = {
    upcoming: { className: "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300", label: "Upcoming" },
    live: { className: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300", label: "Live" },
    completed: { className: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400", label: "Completed" },
  };
  const v = variants[status] ?? variants.upcoming;
  return <Badge className={v.className}>{v.label}</Badge>;
}

// ---------------------------------------------------------------------------
// Active event banner
// ---------------------------------------------------------------------------

function ActiveEventBanner({
  events,
  onActivate,
  onDeactivate,
}: {
  events: Event[];
  onActivate: () => void;
  onDeactivate: (id: string) => void;
}) {
  const liveEvent = events.find((e) => e.status === "live");

  if (liveEvent) {
    const start = new Date(liveEvent.start_date + "T00:00:00");
    const today = new Date();
    const dayIndex = Math.floor((today.getTime() - start.getTime()) / 86400000) + 1;
    const totalDays = Math.floor(
      (new Date(liveEvent.end_date + "T00:00:00").getTime() - start.getTime()) / 86400000
    ) + 1;

    return (
      <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-4 dark:border-emerald-900 dark:bg-emerald-950/50">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="flex h-8 w-8 items-center justify-center rounded-full bg-emerald-500 text-white">
              <Zap className="h-4 w-4" />
            </div>
            <div>
              <p className="font-semibold text-emerald-900 dark:text-emerald-100">
                {liveEvent.name}
              </p>
              <p className="text-sm text-emerald-700 dark:text-emerald-300">
                Day {dayIndex} of {totalDays} &middot; Reports pulling every 30 min
              </p>
            </div>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => onDeactivate(liveEvent.id)}
            className="border-emerald-300 text-emerald-700 hover:bg-emerald-100 dark:border-emerald-700 dark:text-emerald-300"
          >
            <Square className="mr-1.5 h-3.5 w-3.5" />
            End Event
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-zinc-200 bg-zinc-50 p-4 dark:border-zinc-800 dark:bg-zinc-900/50">
      <div className="flex items-center justify-between">
        <div>
          <p className="font-medium text-zinc-700 dark:text-zinc-300">No active event</p>
          <p className="text-sm text-zinc-500">
            Start an event to begin hourly report pulls and Slack updates
          </p>
        </div>
        <Button size="sm" onClick={onActivate}>
          <Play className="mr-1.5 h-3.5 w-3.5" />
          Go Live
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Create / Edit Event Dialog
// ---------------------------------------------------------------------------

const NO_PRIOR_EVENT = "__none__";

// ---------------------------------------------------------------------------
// Manual prior-year ads editor helpers
//
// Amazon Ads' reporting API only retains ~95 days, so an event a full year in
// the past can no longer be pulled for Spend / PPC Sales. Operators enter those
// figures by hand here; the midnight recap uses this event's manual ads as the
// year-over-year baseline when another event links to it.
// ---------------------------------------------------------------------------

interface ManualAdsRow {
  marketplace: string;
  date: string;
  spend: string;
  ppc_sales: string;
}

function manualAdsToRows(manualAds?: ManualAds): ManualAdsRow[] {
  if (!manualAds) return [];
  const rows: ManualAdsRow[] = [];
  for (const [marketplace, byDate] of Object.entries(manualAds)) {
    for (const [date, entry] of Object.entries(byDate)) {
      rows.push({
        marketplace,
        date,
        spend: entry.spend != null ? String(entry.spend) : "",
        ppc_sales: entry.ppc_sales != null ? String(entry.ppc_sales) : "",
      });
    }
  }
  return rows.sort((a, b) =>
    a.marketplace === b.marketplace
      ? a.date.localeCompare(b.date)
      : a.marketplace.localeCompare(b.marketplace),
  );
}

function rowsToManualAds(rows: ManualAdsRow[]): ManualAds {
  const out: ManualAds = {};
  for (const row of rows) {
    if (!row.marketplace || !row.date) continue;
    const spend = Number(row.spend);
    const ppc = Number(row.ppc_sales);
    if (!row.spend && !row.ppc_sales) continue;
    out[row.marketplace] ??= {};
    out[row.marketplace][row.date] = {
      spend: Number.isFinite(spend) ? spend : 0,
      ppc_sales: Number.isFinite(ppc) ? ppc : 0,
    };
  }
  return out;
}

function EventDialog({
  open,
  onOpenChange,
  initial,
  events,
  onSubmit,
  loading,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initial?: Event;
  events: Event[];
  onSubmit: (data: {
    name: string;
    start_date: string;
    end_date: string;
    prior_event_id: string;
    manual_ads: ManualAds;
  }) => void;
  loading: boolean;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [startDate, setStartDate] = useState(initial?.start_date ?? "");
  const [endDate, setEndDate] = useState(initial?.end_date ?? "");
  const [priorEventId, setPriorEventId] = useState(
    initial?.prior_event_id ?? NO_PRIOR_EVENT,
  );
  const [manualAdsRows, setManualAdsRows] = useState<ManualAdsRow[]>(
    manualAdsToRows(initial?.manual_ads),
  );

  const updateManualAdsRow = (index: number, patch: Partial<ManualAdsRow>) => {
    setManualAdsRows((rows) =>
      rows.map((row, i) => (i === index ? { ...row, ...patch } : row)),
    );
  };
  const addManualAdsRow = () => {
    setManualAdsRows((rows) => [
      ...rows,
      { marketplace: MARKETPLACES[0]?.id ?? "US", date: startDate, spend: "", ppc_sales: "" },
    ]);
  };
  const removeManualAdsRow = (index: number) => {
    setManualAdsRows((rows) => rows.filter((_, i) => i !== index));
  };

  const isEdit = !!initial;

  // Any other event can be linked as the prior-year comparison source.
  const priorOptions = events.filter((e) => e.id !== initial?.id);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit Event" : "Create Event"}</DialogTitle>
        </DialogHeader>
        <div className="space-y-4 py-2 max-h-[70vh] overflow-y-auto pr-1">
          <div className="space-y-2">
            <Label>Event Name</Label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Prime Day 2026"
            />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>Start Date</Label>
              <Input
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label>End Date</Label>
              <Input
                type="date"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
              />
            </div>
          </div>
          <div className="space-y-2">
            <Label>Prior-Year Event (for YoY)</Label>
            <Select
              value={priorEventId}
              onValueChange={(v) => v && setPriorEventId(v)}
            >
              <SelectTrigger>
                <SelectValue placeholder="None — no year-over-year comparison" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NO_PRIOR_EVENT}>
                  None — no year-over-year comparison
                </SelectItem>
                {priorOptions.map((e) => (
                  <SelectItem key={e.id} value={e.id}>
                    {e.name} ({e.start_date} — {e.end_date})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              Link last year's equivalent event so hourly SKU lines and the
              midnight recap can show year-over-year stats. Leave as None to
              omit YoY.
            </p>
          </div>

          <div className="space-y-2 rounded-md border p-3">
            <div className="flex items-center justify-between">
              <Label>Manual Ads (prior-year baseline)</Label>
              <Button type="button" variant="outline" size="sm" onClick={addManualAdsRow}>
                <Plus className="mr-1 h-3.5 w-3.5" />
                Add row
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              Enter this event's actual Spend &amp; PPC Sales per marketplace and
              day. Used as the year-over-year baseline when another event links to
              this one and Amazon Ads can no longer pull the data (older than
              ~95 days). Total Sales still comes from the connector.
            </p>
            {manualAdsRows.length === 0 ? (
              <p className="text-xs text-muted-foreground italic">
                No manual ads entered.
              </p>
            ) : (
              <div className="space-y-2">
                <div className="grid grid-cols-[5rem_1fr_1fr_1fr_2rem] items-center gap-2 text-xs font-medium text-muted-foreground">
                  <span>Market</span>
                  <span>Date</span>
                  <span>Spend</span>
                  <span>PPC Sales</span>
                  <span />
                </div>
                {manualAdsRows.map((row, i) => (
                  <div
                    key={i}
                    className="grid grid-cols-[5rem_1fr_1fr_1fr_2rem] items-center gap-2"
                  >
                    <Select
                      value={row.marketplace}
                      onValueChange={(v) => v && updateManualAdsRow(i, { marketplace: v })}
                    >
                      <SelectTrigger className="h-8">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {MARKETPLACES.map((mkt) => (
                          <SelectItem key={mkt.id} value={mkt.id}>
                            {mkt.id}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <Input
                      type="date"
                      className="h-8"
                      min={startDate || undefined}
                      max={endDate || undefined}
                      value={row.date}
                      onChange={(e) => updateManualAdsRow(i, { date: e.target.value })}
                    />
                    <Input
                      type="number"
                      step="0.01"
                      min="0"
                      className="h-8"
                      placeholder="0.00"
                      value={row.spend}
                      onChange={(e) => updateManualAdsRow(i, { spend: e.target.value })}
                    />
                    <Input
                      type="number"
                      step="0.01"
                      min="0"
                      className="h-8"
                      placeholder="0.00"
                      value={row.ppc_sales}
                      onChange={(e) => updateManualAdsRow(i, { ppc_sales: e.target.value })}
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-xs"
                      onClick={() => removeManualAdsRow(i)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            disabled={!name || !startDate || !endDate || loading}
            onClick={() =>
              onSubmit({
                name,
                start_date: startDate,
                end_date: endDate,
                prior_event_id:
                  priorEventId === NO_PRIOR_EVENT ? "" : priorEventId,
                manual_ads: rowsToManualAds(manualAdsRows),
              })
            }
          >
            {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {isEdit ? "Save" : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Go Live dialog — pick event to activate
// ---------------------------------------------------------------------------

function GoLiveDialog({
  open,
  onOpenChange,
  events,
  onActivate,
  loading,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  events: Event[];
  onActivate: (eventId: string) => void;
  loading: boolean;
}) {
  const activatable = events.filter((e) => e.status === "upcoming");
  const [selectedId, setSelectedId] = useState(activatable[0]?.id ?? "");

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Go Live</DialogTitle>
        </DialogHeader>
        <div className="space-y-3 py-2">
          {activatable.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No upcoming events. Create one first.
            </p>
          ) : (
            <>
              <Label>Select event to activate</Label>
              <Select value={selectedId} onValueChange={(v) => v && setSelectedId(v)}>
                <SelectTrigger>
                  <SelectValue placeholder="Choose event" />
                </SelectTrigger>
                <SelectContent>
                  {activatable.map((e) => (
                    <SelectItem key={e.id} value={e.id}>
                      {e.name} ({e.start_date} — {e.end_date})
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            disabled={!selectedId || loading}
            onClick={() => onActivate(selectedId)}
          >
            {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            Activate
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Bot Config Edit Dialog
// ---------------------------------------------------------------------------

function BotConfigDialog({
  open,
  onOpenChange,
  client,
  initial,
  onSubmit,
  loading,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  client: Client;
  initial?: BotConfig;
  onSubmit: (data: Partial<BotConfig>) => void;
  loading: boolean;
}) {
  const initialChannels = getConfigChannels(initial);
  const [channels, setChannels] = useState<SlackChannel[]>(
    initialChannels.length > 0 ? initialChannels : [{ id: "", name: "" }],
  );
  const [baseCurrency, setBaseCurrency] = useState(initial?.base_currency ?? "USD");
  const [tz, setTz] = useState(initial?.client_timezone ?? "America/Los_Angeles");
  const [marketplaces, setMarketplaces] = useState<string[]>(
    initial?.marketplaces ?? client.marketplaces ?? [],
  );
  const [enabled, setEnabled] = useState(initial?.hourly_bot?.enabled ?? false);
  const [dailyRecapEnabled, setDailyRecapEnabled] = useState(
    initial?.daily_recap_enabled ?? false,
  );
  const [skuBreakdownEnabled, setSkuBreakdownEnabled] = useState(
    initial?.sku_breakdown_enabled ?? false,
  );
  const [testChannelId, setTestChannelId] = useState(initial?.test_channel_id ?? "");
  const [useTest, setUseTest] = useState(initial?.use_test_channel ?? false);

  const toggleMarketplace = (mkt: string) => {
    setMarketplaces((prev) =>
      prev.includes(mkt) ? prev.filter((m) => m !== mkt) : [...prev, mkt],
    );
  };

  const updateChannel = (index: number, field: keyof SlackChannel, value: string) => {
    setChannels((prev) => prev.map((c, i) => (i === index ? { ...c, [field]: value } : c)));
  };

  const updateChannelTagIds = (index: number, tagUserIds: string[]) => {
    setChannels((prev) =>
      prev.map((c, i) => (i === index ? { ...c, tag_user_ids: tagUserIds } : c)),
    );
  };
  const addChannel = () => setChannels((prev) => [...prev, { id: "", name: "" }]);
  const removeChannel = (index: number) =>
    setChannels((prev) => (prev.length > 1 ? prev.filter((_, i) => i !== index) : prev));

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Bot Config — {client.name}</DialogTitle>
        </DialogHeader>
        <div className="space-y-4 py-2 max-h-[65vh] overflow-y-auto pr-1">
          <div className="flex items-center justify-between rounded-md border p-3">
            <div>
              <p className="text-sm font-medium">Hourly Bot Enabled</p>
              <p className="text-xs text-muted-foreground">Posts during active events</p>
            </div>
            <Switch checked={enabled} onCheckedChange={setEnabled} />
          </div>

          <div className="flex items-center justify-between rounded-md border p-3">
            <div>
              <p className="text-sm font-medium">Daily Recap Enabled</p>
              <p className="text-xs text-muted-foreground">
                Year-round morning recap of the previous day's account totals
              </p>
            </div>
            <Switch checked={dailyRecapEnabled} onCheckedChange={setDailyRecapEnabled} />
          </div>

          <div className="flex items-center justify-between rounded-md border p-3">
            <div>
              <p className="text-sm font-medium">Per-SKU Breakdown</p>
              <p className="text-xs text-muted-foreground">
                Appends a per-SKU breakdown to hourly drops and the day-end recap
              </p>
            </div>
            <Switch checked={skuBreakdownEnabled} onCheckedChange={setSkuBreakdownEnabled} />
          </div>

          <div className="space-y-2">
            <Label>Slack Channels</Label>
            <p className="text-xs text-muted-foreground">
              Every update is posted to all channels below.
            </p>
            <div className="space-y-2">
              {channels.map((channel, index) => (
                <div key={index} className="space-y-1.5 rounded-md border p-2">
                  <div className="flex items-center gap-2">
                    <Input
                      value={channel.id}
                      onChange={(e) => updateChannel(index, "id", e.target.value)}
                      placeholder="C07XXXXXX"
                      className="flex-1"
                    />
                    <Input
                      value={channel.name ?? ""}
                      onChange={(e) => updateChannel(index, "name", e.target.value)}
                      placeholder="#acme-reports"
                      className="flex-1"
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-xs"
                      disabled={channels.length === 1}
                      onClick={() => removeChannel(index)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                  <MultiSelectDropdown
                    label="Tag on notify"
                    options={SLACK_USERS.map((u) => ({ id: u.id, label: u.name }))}
                    selected={channel.tag_user_ids ?? []}
                    onChange={(ids) => updateChannelTagIds(index, ids)}
                  />
                </div>
              ))}
            </div>
            <Button type="button" variant="outline" size="sm" onClick={addChannel}>
              <Plus className="mr-1 h-4 w-4" />
              Add channel
            </Button>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>Base Currency</Label>
              <Select value={baseCurrency} onValueChange={(v) => v && setBaseCurrency(v)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {CURRENCIES.map((c) => (
                    <SelectItem key={c} value={c}>{c}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>Timezone</Label>
              <Select value={tz} onValueChange={(v) => v && setTz(v)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {CLIENT_TIMEZONES.map((t) => (
                    <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="space-y-2">
            <Label>Marketplaces</Label>
            <div className="flex flex-wrap gap-2">
              {MARKETPLACES.map((mkt) => (
                <label
                  key={mkt.id}
                  className="flex cursor-pointer items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-sm transition-colors hover:bg-accent"
                >
                  <Checkbox
                    checked={marketplaces.includes(mkt.id)}
                    onCheckedChange={() => toggleMarketplace(mkt.id)}
                  />
                  <span>{mkt.flag} {mkt.id}</span>
                </label>
              ))}
            </div>
          </div>

          <div className="space-y-3 rounded-md border p-3">
            <div className="flex items-center justify-between">
              <p className="text-sm font-medium">Test Mode</p>
              <Switch checked={useTest} onCheckedChange={setUseTest} />
            </div>
            {useTest && (
              <div className="space-y-2">
                <Label>Test Channel ID</Label>
                <Input
                  value={testChannelId}
                  onChange={(e) => setTestChannelId(e.target.value)}
                  placeholder="C07YYYYYY"
                />
              </div>
            )}
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            disabled={loading}
            onClick={() =>
              onSubmit({
                channels: channels.filter((c) => c.id.trim()),
                base_currency: baseCurrency,
                client_timezone: tz,
                marketplaces,
                hourly_bot: { enabled },
                daily_recap_enabled: dailyRecapEnabled,
                sku_breakdown_enabled: skuBreakdownEnabled,
                test_channel_id: testChannelId,
                use_test_channel: useTest,
              })
            }
          >
            {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export function SlackBots() {
  const { data: events = [], isLoading: eventsLoading } = useEvents();
  const { data: botConfigs = [], isLoading: configsLoading } = useBotConfigs();
  const { data: clients = [] } = useClients();

  const createEvent = useCreateEvent();
  const updateEvent = useUpdateEvent();
  const deleteEvent = useDeleteEvent();
  const activateEvent = useActivateEvent();
  const deactivateEvent = useDeactivateEvent();
  const upsertBotConfig = useUpsertBotConfig();

  const [showCreateEvent, setShowCreateEvent] = useState(false);
  const [editingEvent, setEditingEvent] = useState<Event | undefined>();
  const [showGoLive, setShowGoLive] = useState(false);
  const [editingBotConfig, setEditingBotConfig] = useState<{
    client: Client;
    config?: BotConfig;
  } | null>(null);

  const configByClientId = new Map(botConfigs.map((c) => [c.client_id, c]));
  const eventNameById = new Map(events.map((e) => [e.id, e.name]));

  const handleCreateEvent = (data: {
    name: string;
    start_date: string;
    end_date: string;
    prior_event_id: string;
    manual_ads: ManualAds;
  }) => {
    createEvent.mutate(data, {
      onSuccess: () => {
        toast.success("Event created");
        setShowCreateEvent(false);
      },
      onError: (err) => toast.error(err.message),
    });
  };

  const handleUpdateEvent = (data: {
    name: string;
    start_date: string;
    end_date: string;
    prior_event_id: string;
    manual_ads: ManualAds;
  }) => {
    if (!editingEvent) return;
    updateEvent.mutate({ id: editingEvent.id, ...data }, {
      onSuccess: () => {
        toast.success("Event updated");
        setEditingEvent(undefined);
      },
      onError: (err) => toast.error(err.message),
    });
  };

  const handleDeleteEvent = (id: string) => {
    deleteEvent.mutate(id, {
      onSuccess: () => toast.success("Event deleted"),
      onError: (err) => toast.error(err.message),
    });
  };

  const handleActivate = (eventId: string) => {
    activateEvent.mutate(eventId, {
      onSuccess: () => {
        toast.success("Event is now live");
        setShowGoLive(false);
      },
      onError: (err) => toast.error(err.message),
    });
  };

  const handleDeactivate = (id: string) => {
    deactivateEvent.mutate(id, {
      onSuccess: () => toast.success("Event ended"),
      onError: (err) => toast.error(err.message),
    });
  };

  const handleSaveBotConfig = (data: Partial<BotConfig>) => {
    if (!editingBotConfig) return;
    upsertBotConfig.mutate(
      { clientId: editingBotConfig.client.id, ...data },
      {
        onSuccess: () => {
          toast.success("Bot config saved");
          setEditingBotConfig(null);
        },
        onError: (err) => toast.error(err.message),
      },
    );
  };

  if (eventsLoading || configsLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Slack Bots</h1>
        <p className="text-muted-foreground">
          Manage events and configure hourly Slack updates for clients
        </p>
      </div>

      {/* Active Event Banner */}
      <ActiveEventBanner
        events={events}
        onActivate={() => setShowGoLive(true)}
        onDeactivate={handleDeactivate}
      />

      {/* Events Section */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-lg">
            <Calendar className="h-5 w-5" />
            Events
          </CardTitle>
          <Button size="sm" onClick={() => setShowCreateEvent(true)}>
            <Plus className="mr-1.5 h-3.5 w-3.5" />
            Create Event
          </Button>
        </CardHeader>
        <CardContent>
          {events.length === 0 ? (
            <EmptyState
              icon={Calendar}
              title="No events"
              description="Create an event to schedule hourly report pulls during sales events"
            />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Start</TableHead>
                  <TableHead>End</TableHead>
                  <TableHead>Prior Year</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="w-10" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {events.map((event) => (
                  <TableRow key={event.id}>
                    <TableCell className="font-medium">{event.name}</TableCell>
                    <TableCell>{event.start_date}</TableCell>
                    <TableCell>{event.end_date}</TableCell>
                    <TableCell>
                      {event.prior_event_id && eventNameById.get(event.prior_event_id) ? (
                        <span className="text-sm">
                          {eventNameById.get(event.prior_event_id)}
                        </span>
                      ) : (
                        <span className="text-sm text-muted-foreground">—</span>
                      )}
                    </TableCell>
                    <TableCell>
                      <EventStatusBadge status={event.status} />
                    </TableCell>
                    <TableCell>
                      <DropdownMenu>
                        <DropdownMenuTrigger
                          render={
                            <Button variant="ghost" size="icon-xs">
                              <MoreHorizontal className="h-4 w-4" />
                            </Button>
                          }
                        />
                        <DropdownMenuContent align="end">
                          {event.status !== "live" && event.status !== "completed" && (
                            <DropdownMenuItem onClick={() => handleActivate(event.id)}>
                              <Play className="mr-2 h-4 w-4" />
                              Go Live
                            </DropdownMenuItem>
                          )}
                          {event.status === "live" && (
                            <DropdownMenuItem onClick={() => handleDeactivate(event.id)}>
                              <Square className="mr-2 h-4 w-4" />
                              End Event
                            </DropdownMenuItem>
                          )}
                          <DropdownMenuItem onClick={() => setEditingEvent(event)}>
                            <Pencil className="mr-2 h-4 w-4" />
                            Edit
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            variant="destructive"
                            onClick={() => handleDeleteEvent(event.id)}
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

      {/* Bot Configuration Section */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-lg">
            <MessageSquare className="h-5 w-5" />
            Client Bot Configuration
          </CardTitle>
        </CardHeader>
        <CardContent>
          {clients.length === 0 ? (
            <EmptyState
              icon={MessageSquare}
              title="No clients"
              description="Add clients on the Clients page first"
            />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Client</TableHead>
                  <TableHead>Slack Channel</TableHead>
                  <TableHead>Marketplaces</TableHead>
                  <TableHead>Hourly Bot</TableHead>
                  <TableHead>Test Mode</TableHead>
                  <TableHead className="w-10" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {clients.map((client) => {
                  const config = configByClientId.get(client.id);
                  const isEnabled = config?.hourly_bot?.enabled ?? false;
                  const channels = getConfigChannels(config);

                  return (
                    <TableRow key={client.id}>
                      <TableCell className="font-medium">{client.name}</TableCell>
                      <TableCell>
                        {channels.length > 0 ? (
                          <span className="text-sm">
                            {channels[0].name ?? channels[0].id}
                            {channels.length > 1 && (
                              <span className="text-muted-foreground"> +{channels.length - 1} more</span>
                            )}
                          </span>
                        ) : (
                          <span className="text-sm text-muted-foreground">Not configured</span>
                        )}
                      </TableCell>
                      <TableCell>
                        <div className="flex flex-wrap gap-1">
                          {(config?.marketplaces ?? []).map((mkt) => (
                            <Badge key={mkt} variant="secondary" className="text-xs">
                              {mkt}
                            </Badge>
                          ))}
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge
                          className={
                            isEnabled
                              ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300"
                              : ""
                          }
                          variant={isEnabled ? "default" : "secondary"}
                        >
                          {isEnabled ? "Enabled" : "Disabled"}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        {config?.use_test_channel && (
                          <Badge variant="outline" className="text-xs">
                            Test
                          </Badge>
                        )}
                      </TableCell>
                      <TableCell>
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          onClick={() => setEditingBotConfig({ client, config })}
                        >
                          <Pencil className="h-4 w-4" />
                        </Button>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Dialogs */}
      <EventDialog
        open={showCreateEvent}
        onOpenChange={setShowCreateEvent}
        events={events}
        onSubmit={handleCreateEvent}
        loading={createEvent.isPending}
      />

      {editingEvent && (
        <EventDialog
          open
          onOpenChange={(open) => !open && setEditingEvent(undefined)}
          initial={editingEvent}
          events={events}
          onSubmit={handleUpdateEvent}
          loading={updateEvent.isPending}
        />
      )}

      <GoLiveDialog
        open={showGoLive}
        onOpenChange={setShowGoLive}
        events={events}
        onActivate={handleActivate}
        loading={activateEvent.isPending}
      />

      {editingBotConfig && (
        <BotConfigDialog
          open
          onOpenChange={(open) => !open && setEditingBotConfig(null)}
          client={editingBotConfig.client}
          initial={editingBotConfig.config}
          onSubmit={handleSaveBotConfig}
          loading={upsertBotConfig.isPending}
        />
      )}
    </div>
  );
}
