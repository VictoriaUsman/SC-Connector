import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { Timeframe, TimeframeStrategy } from "@/types";
import { DAYS_OF_WEEK, TIMEFRAME_STRATEGIES } from "@/types";

const STRATEGY_META: Record<
  TimeframeStrategy,
  { description: string; example: string }
> = {
  yesterday: {
    description:
      "Pull the previous day's data. Standard for most daily reports.",
    example: "If run Mar 20 \u2192 pulls Mar 19",
  },
  today: {
    description:
      "Pull the current day's data so far. Best for hourly reports that need intraday numbers.",
    example: "If run Mar 20 \u2192 pulls Mar 20",
  },
  last_n_days: {
    description:
      "Pull a trailing window of N days. Use \u2018Data delay\u2019 to shift the end date back when Amazon data is delayed.",
    example: "30 days, 3-day delay: Feb 16 \u2013 Mar 16",
  },
  rolling_window: {
    description:
      "Pull a custom range defined by start and end offsets from today.",
    example: "Start \u22122, End \u22121: Mar 18 \u2013 Mar 19",
  },
  last_calendar_week: {
    description:
      "Pull the most recent full week. Choose which day the week starts on.",
    example: "Week starts Thu: Thu Mar 12 \u2013 Wed Mar 18",
  },
  last_calendar_month: {
    description:
      "Pull the entire previous calendar month. Ideal for monthly financial reports.",
    example: "If run in March \u2192 pulls Feb 1 \u2013 Feb 28",
  },
  prior_year_window: {
    description:
      "Pull a window around today\u2019s date shifted back N years. Great for year-over-year comparisons.",
    example: "30d before/after, 1yr back: Apr 21 \u2013 Jun 20 last year",
  },
  custom_range: {
    description:
      "Pull a fixed start\u2013end date range. The same dates every run \u2014 best for one-off backfills, not recurring schedules.",
    example: "Jan 1 \u2013 Jan 15, 2026 (unchanged on future runs)",
  },
};

function yesterdayIso(): string {
  const d = new Date();
  d.setDate(d.getDate() - 1);
  return d.toISOString().slice(0, 10);
}

export function TimeframeConfig({
  value,
  onChange,
}: {
  value: Timeframe;
  onChange: (tf: Timeframe) => void;
}) {
  const strategy = value.strategy;

  const select = (s: TimeframeStrategy) => {
    const base: Timeframe = { strategy: s };
    if (s === "last_n_days") {
      base.days = value.days ?? 30;
      base.end_offset_days = value.end_offset_days ?? 0;
    } else if (s === "rolling_window") {
      base.start_offset = value.start_offset ?? -7;
      base.end_offset = value.end_offset ?? -1;
    } else if (s === "last_calendar_week") {
      base.week_start = value.week_start ?? 0;
    } else if (s === "prior_year_window") {
      base.days_before = value.days_before ?? 30;
      base.days_after = value.days_after ?? 30;
      base.years_back = value.years_back ?? 1;
      base.anchor_offset_days = value.anchor_offset_days ?? 0;
    } else if (s === "custom_range") {
      base.start_date = value.start_date ?? yesterdayIso();
      base.end_date = value.end_date ?? yesterdayIso();
    }
    onChange(base);
  };

  return (
    <div className="space-y-3">
      <Label>Report Timeframe</Label>

      <div className="grid grid-cols-2 gap-2">
        {TIMEFRAME_STRATEGIES.map((s) => {
          const meta = STRATEGY_META[s.value];
          const selected = strategy === s.value;

          return (
            <button
              key={s.value}
              type="button"
              onClick={() => select(s.value)}
              className={`relative text-left rounded-lg border p-3 transition-colors ${
                selected
                  ? "border-primary bg-primary/5 ring-1 ring-primary/20"
                  : "border-border hover:border-muted-foreground/30 hover:bg-accent/50"
              }`}
            >
              <div className="flex items-start gap-2">
                <span
                  className={`mt-0.5 h-3.5 w-3.5 shrink-0 rounded-full border-2 transition-colors ${
                    selected
                      ? "border-primary bg-primary"
                      : "border-muted-foreground/40"
                  }`}
                >
                  {selected && (
                    <span className="flex h-full w-full items-center justify-center">
                      <span className="h-1.5 w-1.5 rounded-full bg-primary-foreground" />
                    </span>
                  )}
                </span>
                <div className="min-w-0">
                  <span className="text-sm font-medium">{s.label}</span>
                  <p className="mt-0.5 text-xs text-muted-foreground leading-snug">
                    {meta.description}
                  </p>
                  <p className="mt-1 text-[11px] italic text-muted-foreground/70">
                    {meta.example}
                  </p>
                </div>
              </div>
            </button>
          );
        })}
      </div>

      {strategy === "last_n_days" && (
        <div className="grid grid-cols-2 gap-4 rounded-md border border-dashed p-3">
          <div className="space-y-1.5">
            <Label className="text-xs">Number of days</Label>
            <Input
              type="number"
              min={1}
              max={365}
              value={value.days ?? 30}
              onChange={(e) =>
                onChange({ ...value, days: Math.max(1, Number(e.target.value)) })
              }
              className="h-8"
            />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs">Data delay (days)</Label>
            <Input
              type="number"
              min={0}
              max={30}
              value={value.end_offset_days ?? 0}
              onChange={(e) =>
                onChange({
                  ...value,
                  end_offset_days: Math.max(0, Number(e.target.value)),
                })
              }
              className="h-8"
            />
            <p className="text-[11px] text-muted-foreground">
              Shift end date back for delayed Amazon data
            </p>
          </div>
        </div>
      )}

      {strategy === "rolling_window" && (
        <div className="grid grid-cols-2 gap-4 rounded-md border border-dashed p-3">
          <div className="space-y-1.5">
            <Label className="text-xs">Start offset (days from today)</Label>
            <Input
              type="number"
              min={-730}
              max={0}
              value={value.start_offset ?? -7}
              onChange={(e) => {
                const n = Number(e.target.value);
                if (!Number.isNaN(n))
                  onChange({ ...value, start_offset: Math.min(0, n) });
              }}
              className="h-8"
            />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs">End offset (days from today)</Label>
            <Input
              type="number"
              min={-730}
              max={0}
              value={value.end_offset ?? -1}
              onChange={(e) => {
                const n = Number(e.target.value);
                if (!Number.isNaN(n))
                  onChange({ ...value, end_offset: Math.min(0, n) });
              }}
              className="h-8"
            />
          </div>
          <p className="col-span-2 text-[11px] text-muted-foreground">
            Use negative values for past dates (e.g. -7 = 7 days ago)
          </p>
        </div>
      )}

      {strategy === "last_calendar_week" && (
        <div className="rounded-md border border-dashed p-3 space-y-1.5">
          <Label className="text-xs">Week starts on</Label>
          <div className="flex gap-1">
            {DAYS_OF_WEEK.map((d) => (
              <button
                key={d.value}
                type="button"
                onClick={() => onChange({ ...value, week_start: d.value })}
                className={`px-2.5 py-1 rounded text-xs font-medium border transition-colors ${
                  (value.week_start ?? 0) === d.value
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

      {strategy === "prior_year_window" && (
        <div className="grid grid-cols-4 gap-4 rounded-md border border-dashed p-3">
          <div className="space-y-1.5">
            <Label className="text-xs">Days before</Label>
            <Input
              type="number"
              min={1}
              max={365}
              value={value.days_before ?? 30}
              onChange={(e) =>
                onChange({
                  ...value,
                  days_before: Math.max(1, Number(e.target.value)),
                })
              }
              className="h-8"
            />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs">Days after</Label>
            <Input
              type="number"
              min={0}
              max={365}
              value={value.days_after ?? 30}
              onChange={(e) =>
                onChange({
                  ...value,
                  days_after: Math.max(0, Number(e.target.value)),
                })
              }
              className="h-8"
            />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs">Years back</Label>
            <Input
              type="number"
              min={1}
              max={5}
              value={value.years_back ?? 1}
              onChange={(e) =>
                onChange({
                  ...value,
                  years_back: Math.max(1, Math.min(5, Number(e.target.value))),
                })
              }
              className="h-8"
            />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs">Data delay (days)</Label>
            <Input
              type="number"
              min={0}
              max={30}
              value={value.anchor_offset_days ?? 0}
              onChange={(e) =>
                onChange({
                  ...value,
                  anchor_offset_days: Math.max(0, Math.min(30, Number(e.target.value))),
                })
              }
              className="h-8"
            />
          </div>
          <p className="col-span-4 text-[11px] text-muted-foreground">
            Window centered on today&apos;s date shifted back by the specified years. Data delay shifts the anchor back for delayed Amazon data.
          </p>
        </div>
      )}

      {strategy === "custom_range" && (
        <div className="grid grid-cols-2 gap-4 rounded-md border border-dashed p-3">
          <div className="space-y-1.5">
            <Label className="text-xs">Start date</Label>
            <Input
              type="date"
              value={value.start_date ?? yesterdayIso()}
              max={value.end_date ?? undefined}
              onChange={(e) => onChange({ ...value, start_date: e.target.value })}
              className="h-8"
            />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs">End date</Label>
            <Input
              type="date"
              value={value.end_date ?? yesterdayIso()}
              min={value.start_date ?? undefined}
              onChange={(e) => onChange({ ...value, end_date: e.target.value })}
              className="h-8"
            />
          </div>
          <p className="col-span-2 text-[11px] text-muted-foreground">
            This range is fixed — it will not shift on future runs. Deactivate the schedule after it runs once, or use it for a manual backfill.
          </p>
        </div>
      )}

      {strategy !== "yesterday" && (
        <Badge variant="secondary" className="text-xs font-normal">
          Reconciliation is skipped for date-range reports
        </Badge>
      )}
    </div>
  );
}

