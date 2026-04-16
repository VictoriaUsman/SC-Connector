import { useMemo } from "react";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { getOutputColumns } from "@/data/report-metadata";
import type { OutputColumnDef } from "@/data/report-metadata";
import { formatReportType } from "@/lib/format";
import { ChevronRight, Columns3 } from "lucide-react";
import { cn } from "@/lib/utils";
import { useState } from "react";

interface OutputColumnsPickerProps {
  reportType: string;
  value: string[] | undefined;
  onChange: (columns: string[] | undefined) => void;
}

export function OutputColumnsPicker({
  reportType,
  value,
  onChange,
}: OutputColumnsPickerProps) {
  const [expanded, setExpanded] = useState(false);
  const sections = getOutputColumns(reportType);

  const allKeys = useMemo(() => {
    if (!sections) return [];
    return Object.values(sections).flatMap((cols) => cols.map((c) => c.key));
  }, [sections]);

  if (!sections) return null;

  const selected = value ? new Set(value) : null;
  const isAllSelected = selected === null || selected.size === allKeys.length;

  const toggleColumn = (key: string) => {
    if (selected === null) {
      const next = allKeys.filter((k) => k !== key);
      onChange(next);
    } else if (selected.has(key)) {
      const next = allKeys.filter((k) => selected.has(k) && k !== key);
      onChange(next.length === 0 ? undefined : next);
    } else {
      const next = allKeys.filter((k) => selected.has(k) || k === key);
      onChange(next.length === allKeys.length ? undefined : next);
    }
  };

  const toggleSection = (sectionCols: OutputColumnDef[], selectAll: boolean) => {
    const sectionKeys = new Set(sectionCols.map((c) => c.key));
    if (selectAll) {
      if (selected === null) return;
      const merged = allKeys.filter(
        (k) => selected.has(k) || sectionKeys.has(k),
      );
      onChange(merged.length === allKeys.length ? undefined : merged);
    } else {
      const next = allKeys.filter(
        (k) => (selected === null || selected.has(k)) && !sectionKeys.has(k),
      );
      onChange(next.length === 0 ? undefined : next);
    }
  };

  const sectionSelectedCount = (cols: OutputColumnDef[]) =>
    selected === null
      ? cols.length
      : cols.filter((c) => selected.has(c.key)).length;

  const selectedTotal = selected === null ? allKeys.length : selected.size;

  return (
    <div className="rounded-md border">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-accent/50 transition-colors rounded-md"
      >
        <ChevronRight
          className={cn(
            "h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform",
            expanded && "rotate-90",
          )}
        />
        <Columns3 className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <span className="text-sm font-medium flex-1 truncate">
          {formatReportType(reportType)} &mdash; Output Columns
        </span>
        <Badge
          variant={isAllSelected ? "secondary" : "default"}
          className="text-[10px] px-1.5 py-0 shrink-0"
        >
          {isAllSelected ? "All" : `${selectedTotal}/${allKeys.length}`}
        </Badge>
      </button>

      {expanded && (
        <div className="border-t px-3 pb-3 pt-2 space-y-3">
          {Object.entries(sections).map(([sectionName, cols]) => {
            const count = sectionSelectedCount(cols);
            const allInSection = count === cols.length;
            return (
              <SectionGroup
                key={sectionName}
                sectionName={sectionName}
                columns={cols}
                selected={selected}
                allInSection={allInSection}
                count={count}
                onToggleColumn={toggleColumn}
                onToggleAll={(selectAll) => toggleSection(cols, selectAll)}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}

function SectionGroup({
  sectionName,
  columns,
  selected,
  allInSection,
  count,
  onToggleColumn,
  onToggleAll,
}: {
  sectionName: string;
  columns: OutputColumnDef[];
  selected: Set<string> | null;
  allInSection: boolean;
  count: number;
  onToggleColumn: (key: string) => void;
  onToggleAll: (selectAll: boolean) => void;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <Label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
          {sectionName}
        </Label>
        <button
          type="button"
          className="text-[10px] text-primary hover:underline"
          onClick={() => onToggleAll(!allInSection)}
        >
          {allInSection ? "Deselect all" : "Select all"} ({count}/{columns.length})
        </button>
      </div>
      <div className="grid gap-1">
        {columns.map((col) => {
          const checked = selected === null || selected.has(col.key);
          return (
            <label
              key={col.key}
              className="flex items-center gap-2 px-1.5 py-0.5 rounded hover:bg-accent/40 cursor-pointer text-xs"
            >
              <Checkbox
                checked={checked}
                onCheckedChange={() => onToggleColumn(col.key)}
              />
              <span className="flex-1 truncate">{col.label}</span>
              <span className="text-[10px] text-muted-foreground font-mono truncate max-w-[200px]">
                {col.key}
              </span>
            </label>
          );
        })}
      </div>
    </div>
  );
}
