import { useMemo, useState } from "react";
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  flexRender,
  type ColumnDef,
  type SortingState,
  type ColumnFiltersState,
} from "@tanstack/react-table";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { DataTableColumnHeader } from "@/components/ui/data-table-column-header";
import { DataTablePagination } from "@/components/ui/data-table-pagination";
import { StatusBadge } from "@/components/status-badge";
import { EmptyState } from "@/components/empty-state";
import { useRealtimeJobs } from "@/hooks/use-jobs";
import { timeAgo, formatApiSource, formatReportType } from "@/lib/format";
import {
  CheckCircle,
  XCircle,
  Loader2,
  Clock,
  Activity,
  ExternalLink,
  FilterX,
} from "lucide-react";
import type { Job, JobStatus } from "@/types";

const IN_PROGRESS_STATUSES: JobStatus[] = [
  "pending",
  "requesting",
  "polling",
  "downloading",
  "uploading",
];

const STATUS_OPTIONS = [
  { value: "completed", label: "Completed" },
  { value: "failed", label: "Failed" },
  { value: "in_progress", label: "In Progress" },
];

const SOURCE_OPTIONS = [
  { value: "sp_api", label: "SP API" },
  { value: "ads_api", label: "Ads API" },
];

const TIME_RANGE_OPTIONS = [
  { value: "1", label: "Last hour" },
  { value: "6", label: "Last 6 hours" },
  { value: "24", label: "Last 24 hours" },
  { value: "168", label: "Last 7 days" },
  { value: "720", label: "Last 30 days" },
];

function statusGroupFilterFn(
  row: { getValue: (id: string) => unknown },
  _columnId: string,
  filterValue: string,
): boolean {
  const status = row.getValue("status") as JobStatus;
  if (filterValue === "in_progress") return IN_PROGRESS_STATUSES.includes(status);
  return status === filterValue;
}

function timeRangeFilterFn(
  row: { getValue: (id: string) => unknown },
  _columnId: string,
  filterValue: string,
): boolean {
  const iso = row.getValue("started_at") as string | undefined;
  if (!iso) return false;
  const hours = Number(filterValue);
  const cutoff = Date.now() - hours * 3_600_000;
  return new Date(iso).getTime() >= cutoff;
}

const columns: ColumnDef<Job>[] = [
  {
    accessorKey: "status",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="Status" />
    ),
    cell: ({ row }) => <StatusBadge status={row.getValue("status")} />,
    filterFn: statusGroupFilterFn,
  },
  {
    accessorKey: "client_id",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="Client" />
    ),
    cell: ({ row }) => (
      <span className="font-medium">{row.getValue("client_id")}</span>
    ),
    filterFn: "includesString",
  },
  {
    accessorKey: "api_source",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="Source" />
    ),
    cell: ({ row }) => formatApiSource(row.getValue("api_source")),
    filterFn: "equals",
  },
  {
    accessorKey: "report_type",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="Report Type" />
    ),
    cell: ({ row }) => (
      <span
        className="max-w-[200px] truncate block"
        title={row.getValue("report_type")}
      >
        {formatReportType(row.getValue("report_type"))}
      </span>
    ),
  },
  {
    accessorKey: "marketplace",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="Marketplace" />
    ),
    filterFn: "equals",
  },
  {
    accessorKey: "report_date",
    header: ({ column }) => (
      <DataTableColumnHeader column={column} title="Report Date" />
    ),
    cell: ({ row }) => row.getValue("report_date") ?? "—",
  },
  {
    id: "drive",
    header: "Drive",
    enableSorting: false,
    cell: ({ row }) => {
      const fileId = row.original.gdrive_file_id;
      if (!fileId) return <span className="text-muted-foreground">—</span>;
      return (
        <a
          href={`https://drive.google.com/file/d/${fileId}/view`}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-primary hover:underline text-xs"
          title={row.original.gdrive_path}
        >
          Open
          <ExternalLink className="h-3 w-3" />
        </a>
      );
    },
  },
  {
    accessorKey: "started_at",
    header: ({ column }) => (
      <DataTableColumnHeader
        column={column}
        title="Started"
        className="justify-end"
      />
    ),
    cell: ({ row }) => (
      <span className="text-muted-foreground">
        {timeAgo(row.getValue("started_at"))}
      </span>
    ),
    filterFn: timeRangeFilterFn,
  },
];

function StatCard({
  title,
  value,
  icon: Icon,
  description,
}: {
  title: string;
  value: number;
  icon: React.ElementType;
  description: string;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
        <Icon className="h-4 w-4 text-muted-foreground" />
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold">{value}</div>
        <p className="text-xs text-muted-foreground">{description}</p>
      </CardContent>
    </Card>
  );
}

export function Dashboard() {
  const { jobs, loading } = useRealtimeJobs({ max: 500 });

  const [sorting, setSorting] = useState<SortingState>([
    { id: "started_at", desc: true },
  ]);
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([]);

  const distinctClients = useMemo(
    () => [...new Set(jobs.map((j) => j.client_id))].sort(),
    [jobs],
  );

  const distinctMarketplaces = useMemo(
    () => [...new Set(jobs.map((j) => j.marketplace))].sort(),
    [jobs],
  );

  const table = useReactTable({
    data: jobs,
    columns,
    state: { sorting, columnFilters },
    onSortingChange: setSorting,
    onColumnFiltersChange: setColumnFilters,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    initialState: { pagination: { pageSize: 25 } },
  });

  const filteredRows = table.getFilteredRowModel().rows;
  const hasFilters = columnFilters.length > 0;

  const stats = useMemo(() => {
    const rows = filteredRows.map((r) => r.original);
    const completed = rows.filter((j) => j.status === "completed").length;
    const failed = rows.filter((j) => j.status === "failed").length;
    const inProgress = rows.filter((j) =>
      IN_PROGRESS_STATUSES.includes(j.status),
    ).length;
    return { completed, failed, inProgress, total: rows.length };
  }, [filteredRows]);

  const statsDescription = hasFilters ? "Filtered results" : "Last 500 jobs";

  const activeStatus =
    (columnFilters.find((f) => f.id === "status")?.value as string) ?? "";
  const activeClient =
    (columnFilters.find((f) => f.id === "client_id")?.value as string) ?? "";
  const activeSource =
    (columnFilters.find((f) => f.id === "api_source")?.value as string) ?? "";
  const activeMarketplace =
    (columnFilters.find((f) => f.id === "marketplace")?.value as string) ?? "";
  const activeTimeRange =
    (columnFilters.find((f) => f.id === "started_at")?.value as string) ?? "";

  function setFilter(id: string, value: string | null) {
    setColumnFilters((prev) => {
      const next = prev.filter((f) => f.id !== id);
      if (value) next.push({ id, value });
      return next;
    });
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold tracking-tight">Dashboard</h1>

      <div className="grid gap-4 md:grid-cols-4">
        <StatCard
          title="Completed"
          value={stats.completed}
          icon={CheckCircle}
          description={statsDescription}
        />
        <StatCard
          title="Failed"
          value={stats.failed}
          icon={XCircle}
          description={statsDescription}
        />
        <StatCard
          title="In Progress"
          value={stats.inProgress}
          icon={Clock}
          description={statsDescription}
        />
        <StatCard
          title="Total Jobs"
          value={stats.total}
          icon={Activity}
          description={statsDescription}
        />
      </div>

      <Card>
        <CardHeader className="pb-4">
          <CardTitle>Jobs</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Filter toolbar */}
          <div className="flex flex-wrap items-center gap-2">
            <Select
              value={activeStatus}
              onValueChange={(v) => setFilter("status", v)}
            >
              <SelectTrigger size="sm" className="w-[130px]">
                <SelectValue placeholder="Status" />
              </SelectTrigger>
              <SelectContent>
                {STATUS_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Select
              value={activeClient}
              onValueChange={(v) => setFilter("client_id", v)}
            >
              <SelectTrigger size="sm" className="w-[140px]">
                <SelectValue placeholder="Client" />
              </SelectTrigger>
              <SelectContent>
                {distinctClients.map((c) => (
                  <SelectItem key={c} value={c}>
                    {c}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Select
              value={activeSource}
              onValueChange={(v) => setFilter("api_source", v)}
            >
              <SelectTrigger size="sm" className="w-[120px]">
                <SelectValue placeholder="Source" />
              </SelectTrigger>
              <SelectContent>
                {SOURCE_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Select
              value={activeMarketplace}
              onValueChange={(v) => setFilter("marketplace", v)}
            >
              <SelectTrigger size="sm" className="w-[130px]">
                <SelectValue placeholder="Marketplace" />
              </SelectTrigger>
              <SelectContent>
                {distinctMarketplaces.map((m) => (
                  <SelectItem key={m} value={m}>
                    {m}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Select
              value={activeTimeRange}
              onValueChange={(v) => setFilter("started_at", v)}
            >
              <SelectTrigger size="sm" className="w-[140px]">
                <SelectValue placeholder="Time range" />
              </SelectTrigger>
              <SelectContent>
                {TIME_RANGE_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Input
              placeholder="Search report type…"
              className="h-7 w-[180px] text-sm"
              value={
                (table.getColumn("report_type")?.getFilterValue() as string) ??
                ""
              }
              onChange={(e) =>
                table
                  .getColumn("report_type")
                  ?.setFilterValue(e.target.value || undefined)
              }
            />

            {hasFilters && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setColumnFilters([])}
              >
                <FilterX className="mr-1 h-3.5 w-3.5" />
                Clear
              </Button>
            )}
          </div>

          {/* Table */}
          {table.getRowModel().rows.length === 0 ? (
            <EmptyState
              icon={Activity}
              title={hasFilters ? "No matching jobs" : "No jobs yet"}
              description={
                hasFilters
                  ? "Try adjusting your filters."
                  : "Trigger an on-demand report or configure a schedule to get started."
              }
            />
          ) : (
            <>
              <Table>
                <TableHeader>
                  {table.getHeaderGroups().map((headerGroup) => (
                    <TableRow key={headerGroup.id}>
                      {headerGroup.headers.map((header) => (
                        <TableHead key={header.id}>
                          {header.isPlaceholder
                            ? null
                            : flexRender(
                                header.column.columnDef.header,
                                header.getContext(),
                              )}
                        </TableHead>
                      ))}
                    </TableRow>
                  ))}
                </TableHeader>
                <TableBody>
                  {table.getRowModel().rows.map((row) => (
                    <TableRow key={row.id}>
                      {row.getVisibleCells().map((cell) => (
                        <TableCell key={cell.id}>
                          {flexRender(
                            cell.column.columnDef.cell,
                            cell.getContext(),
                          )}
                        </TableCell>
                      ))}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>

              <DataTablePagination table={table} />
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
