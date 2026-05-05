import { useState } from "react";
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  flexRender,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import { useQueryClient } from "@tanstack/react-query";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardAction,
  CardContent,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { DataTableColumnHeader } from "@/components/ui/data-table-column-header";
import { DataTablePagination } from "@/components/ui/data-table-pagination";
import { useAdsProfiles } from "@/hooks/use-ads-profiles";
import { MARKETPLACES, type AdsProfile } from "@/types";
import { RefreshCw, AlertTriangle, Search } from "lucide-react";

const COUNTRY_FLAG: Record<string, string> = Object.fromEntries(
  MARKETPLACES.map((m) => [m.id, m.flag]),
);

function formatBudget(amount: number | undefined | null, currency: string): string {
  if (amount == null) return "-";
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
      maximumFractionDigits: 0,
    }).format(amount);
  } catch {
    return `${currency} ${amount.toLocaleString()}`;
  }
}

const columns: ColumnDef<AdsProfile>[] = [
  {
    accessorKey: "profileId",
    header: ({ column }) => <DataTableColumnHeader column={column} title="Profile ID" />,
    cell: ({ row }) => (
      <span className="font-mono text-xs">{row.original.profileId}</span>
    ),
  },
  {
    accessorKey: "countryCode",
    header: ({ column }) => <DataTableColumnHeader column={column} title="Country" />,
    cell: ({ row }) => {
      const code = row.original.countryCode;
      const flag = COUNTRY_FLAG[code];
      return (
        <span className="inline-flex items-center gap-1.5">
          {flag && <span>{flag}</span>}
          <span>{code}</span>
        </span>
      );
    },
  },
  {
    accessorKey: "currencyCode",
    header: ({ column }) => <DataTableColumnHeader column={column} title="Currency" />,
  },
  {
    accessorKey: "dailyBudget",
    header: ({ column }) => <DataTableColumnHeader column={column} title="Daily Budget" />,
    cell: ({ row }) =>
      formatBudget(row.original.dailyBudget, row.original.currencyCode),
  },
  {
    accessorKey: "timezone",
    header: ({ column }) => <DataTableColumnHeader column={column} title="Timezone" />,
    cell: ({ row }) => (
      <span className="text-xs">{row.original.timezone}</span>
    ),
  },
  {
    id: "accountType",
    accessorFn: (row) => row.accountInfo?.type ?? "",
    header: ({ column }) => <DataTableColumnHeader column={column} title="Account Type" />,
    cell: ({ row }) => {
      const t = row.original.accountInfo?.type;
      return t ? (
        <Badge variant="outline" className="capitalize">
          {t}
        </Badge>
      ) : (
        "-"
      );
    },
  },
  {
    id: "accountName",
    accessorFn: (row) =>
      row.accountInfo?.name || row.accountInfo?.sellerStringId || "",
    header: ({ column }) => <DataTableColumnHeader column={column} title="Account Name" />,
    cell: ({ row }) => {
      const info = row.original.accountInfo;
      return (
        <span className="max-w-[200px] truncate block" title={info?.name || info?.sellerStringId}>
          {info?.name || info?.sellerStringId || "-"}
        </span>
      );
    },
  },
  {
    id: "marketplaceId",
    accessorFn: (row) => row.accountInfo?.marketplaceStringId ?? "",
    header: ({ column }) => <DataTableColumnHeader column={column} title="Marketplace ID" />,
    cell: ({ row }) => (
      <span className="font-mono text-xs">
        {row.original.accountInfo?.marketplaceStringId || "-"}
      </span>
    ),
  },
  {
    id: "linked",
    accessorFn: (row) => row._linked_client_id ?? "",
    header: ({ column }) => <DataTableColumnHeader column={column} title="Linked Client" />,
    cell: ({ row }) => {
      const clientId = row.original._linked_client_id;
      return clientId ? (
        <Badge variant="secondary">{clientId}</Badge>
      ) : (
        <span className="text-muted-foreground text-xs">Unlinked</span>
      );
    },
  },
];

function LoadingSkeleton() {
  return (
    <div className="space-y-3 p-4">
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="flex gap-4">
          {Array.from({ length: 5 }).map((_, j) => (
            <div
              key={j}
              className="h-4 rounded bg-muted animate-pulse"
              style={{ width: `${60 + Math.random() * 80}px` }}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

function AdsProfilesSection() {
  const { data: profiles, isLoading, isError, error, refetch, isFetching } = useAdsProfiles();
  const queryClient = useQueryClient();

  const [sorting, setSorting] = useState<SortingState>([]);
  const [globalFilter, setGlobalFilter] = useState("");

  const table = useReactTable({
    data: profiles ?? [],
    columns,
    state: { sorting, globalFilter },
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    initialState: { pagination: { pageSize: 25 } },
  });

  const handleRefresh = () => {
    queryClient.removeQueries({ queryKey: ["ads-profiles"] });
    refetch();
  };

  return (
    <Card>
      <CardHeader className="border-b">
        <div className="flex items-center gap-2">
          <CardTitle>Amazon Ads Profiles</CardTitle>
          {profiles && (
            <Badge variant="secondary" className="tabular-nums">
              {profiles.length}
            </Badge>
          )}
        </div>
        <CardDescription>
          All advertising profiles visible to the shared Ads API credentials
        </CardDescription>
        <CardAction>
          <Button
            variant="outline"
            size="sm"
            onClick={handleRefresh}
            disabled={isFetching}
          >
            <RefreshCw className={`h-3.5 w-3.5 mr-1.5 ${isFetching ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent className="p-0">
        {isLoading ? (
          <LoadingSkeleton />
        ) : isError ? (
          <div className="flex flex-col items-center justify-center gap-3 py-12">
            <AlertTriangle className="h-8 w-8 text-destructive" />
            <p className="text-sm text-muted-foreground">
              {(error as Error)?.message || "Failed to load profiles"}
            </p>
            <Button variant="outline" size="sm" onClick={handleRefresh}>
              Retry
            </Button>
          </div>
        ) : (
          <>
            <div className="px-4 pt-4 pb-2">
              <div className="relative max-w-sm">
                <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                <Input
                  placeholder="Search profiles..."
                  value={globalFilter}
                  onChange={(e) => setGlobalFilter(e.target.value)}
                  className="pl-9 h-9"
                />
              </div>
            </div>
            <Table>
              <TableHeader>
                {table.getHeaderGroups().map((headerGroup) => (
                  <TableRow key={headerGroup.id}>
                    {headerGroup.headers.map((header) => (
                      <TableHead key={header.id}>
                        {header.isPlaceholder
                          ? null
                          : flexRender(header.column.columnDef.header, header.getContext())}
                      </TableHead>
                    ))}
                  </TableRow>
                ))}
              </TableHeader>
              <TableBody>
                {table.getRowModel().rows.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={columns.length} className="h-24 text-center text-muted-foreground">
                      {globalFilter ? "No profiles match your search." : "No profiles found."}
                    </TableCell>
                  </TableRow>
                ) : (
                  table.getRowModel().rows.map((row) => (
                    <TableRow key={row.id}>
                      {row.getVisibleCells().map((cell) => (
                        <TableCell key={cell.id}>
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </TableCell>
                      ))}
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
            {(profiles?.length ?? 0) > 10 && (
              <div className="border-t">
                <DataTablePagination table={table} />
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

export function Admin() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Admin</h1>
        <p className="text-muted-foreground">System tools and diagnostics</p>
      </div>
      <AdsProfilesSection />
    </div>
  );
}
