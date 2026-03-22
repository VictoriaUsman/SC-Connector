---
name: frontend-react-app
description: React + Vite + shadcn/ui (base-ui) frontend reference for the Kalilos connector. Component catalog, hooks, utilities, patterns, and theme. Use when building or modifying frontend pages, components, forms, tables, or dialogs.
---

# Frontend React App

React 19 + Vite + TypeScript strict + shadcn/ui (built on `@base-ui/react`, NOT Radix) + TanStack Query/Table + Firestore real-time via `reactfire`.

## Quick Reference

- Path alias: `@/` → `frontend/src/`
- Theme: Tailwind v4 with `@theme inline` in `src/index.css`; CSS vars for all semantic colors
- Font: Geist Variable
- Primary color: near-black in light / near-white in dark (neutral `oklch`)
- Toasts: `sonner` — use `toast.success()`, `toast.error()`
- Routing: `react-router-dom` with nested `Layout` + `Outlet`

## UI Components (`components/ui/`)

All primitives from `@base-ui/react`. Use `render` prop (not `asChild`) for polymorphic rendering.

| Component | Primitive | Key Props/Variants |
|-----------|-----------|-------------------|
| Button | `@base-ui/react/button` | variants: default/outline/secondary/ghost/destructive/link; sizes: default/xs/sm/lg/icon/icon-xs/icon-sm/icon-lg |
| Dialog | `@base-ui/react/dialog` | `DialogContent` has `showCloseButton?`; `DialogFooter` has `showCloseButton?` |
| Select | `@base-ui/react/select` | `SelectTrigger` size: sm/default; `SelectContent` has side/align props |
| Switch | `@base-ui/react/switch` | size: sm/default; checked = `data-checked:bg-primary` |
| DropdownMenu | `@base-ui/react/menu` | `DropdownMenuItem` variant: default/destructive; inset prop |
| Checkbox | `@base-ui/react/checkbox` | Uses `CheckIcon` from lucide-react |
| Tabs | `@base-ui/react/tabs` | `TabsList` variant: default/line; orientation: horizontal/vertical |
| Badge | Custom span | variants: default/secondary/destructive/outline/ghost/link |
| Card | Native div | size: default/sm; slots: Card/CardHeader/CardTitle/CardDescription/CardAction/CardContent/CardFooter |
| Table | Native table | Slots: Table/TableHeader/TableBody/TableRow/TableHead/TableCell |
| Input | `@base-ui/react/input` | Standard HTML input props |
| Popover | `@base-ui/react/popover` | Has PopoverHeader/PopoverTitle/PopoverDescription |
| Separator | `@base-ui/react/separator` | orientation: horizontal/vertical |

## Custom Components (`components/`)

| Component | Purpose | Key Props |
|-----------|---------|-----------|
| MultiSelectDropdown | Popover with checkboxes + search | `label`, `options: {id,label}[]`, `selected`, `onChange`, `searchable?`, `maxBadges?` |
| MultiCheckboxSelect | Inline checkbox grid | `label`, `options: {id,label}[]`, `selected`, `onChange`, `renderItem?` |
| ReportSelector | API source + report type + marketplaces | `apiSource`, `reportType`, `marketplaceIds`, `adsConfig` + change handlers |
| AdsReportConfigPanel | Ads column/dimension picker | `reportType`, `value: AdsReportParams`, `onChange` |
| FolderConfig | Drive folder name + subfolder strategy | `folderName`, `subfolderStrategy: "date"|"none"` + change handlers |
| TimeframeConfig | Report date range strategy | `value: Timeframe`, `onChange` |
| StatusBadge | Job status as colored badge | `status: JobStatus` |
| EmptyState | Placeholder with icon/title/action | `icon: LucideIcon`, `title`, `description`, `action?` |
| Layout | App shell with nav + theme toggle | Uses `NavLink` + `Outlet` |

## Hooks (`hooks/`)

| Hook | Returns | Notes |
|------|---------|-------|
| `useClients()` | `useQuery` result | Key: `["clients"]` |
| `useCreateClient()` | `useMutation` | Invalidates `["clients"]` |
| `useUpdateClient()` | `useMutation` | Invalidates `["clients"]` |
| `useDeleteClient()` | `useMutation` | Invalidates `["clients"]` |
| `useSchedules(clientId?)` | `useQuery` result | Key: `["schedules", clientId]` |
| `useCreateSchedule()` | `useMutation` | Invalidates `["schedules"]` |
| `useUpdateSchedule()` | `useMutation` | Invalidates `["schedules"]` |
| `useDeleteSchedule()` | `useMutation` | Invalidates `["schedules"]` |
| `useTriggerSchedule()` | `useMutation` | Invalidates schedules + jobs |
| `useRealtimeJobs(opts?)` | `{ jobs, loading, error }` | Firestore `onSnapshot`; opts: `clientId?`, `status?`, `max?` |
| `useTriggerReport()` | `useMutation` | Invalidates jobs |
| `useAdsReportConfig()` | `useQuery` result | Key: `["ads-report-config"]`, staleTime: 30min |

## Utilities (`lib/`)

- `cn(...inputs)` — `twMerge(clsx(inputs))` for class merging
- `formatDate(iso)` — short date via `toLocaleString` (browser timezone)
- `timeAgo(iso)` — relative time string
- `formatApiSource(source)` — "SP API" / "Ads API"
- `formatReportType(type)` — strips GET_, replaces _, title-cases
- `formatTimeframeLabel(tf)` — human label for timeframe strategy
- `api` object in `lib/api.ts` — all backend API calls, throws `ApiError`

## Types & Constants (`types/index.ts`)

- `ApiSource`: `"sp_api" | "ads_api"`
- `Frequency` / `ScheduleType`: `"hourly" | "daily" | "weekly" | "monthly"`
- `JobStatus`: pending/requesting/polling/downloading/uploading/completed/failed
- `MARKETPLACES`: array of `{id, label, flag}` (US, CA, MX, BR, UK, DE, FR, IT, ES, JP, AU, IN)
- `SP_REPORT_TYPES`: 10 SP API report type strings
- `ADS_REPORT_TYPES`: 8 Ads API report type strings
- `API_SOURCES`, `FREQUENCIES`, `DAYS_OF_WEEK`, `TIMEFRAME_STRATEGIES`: UI option arrays

## Common Patterns

### Dialog with Form

```tsx
<Dialog open={open} onOpenChange={setOpen}>
  <DialogContent>
    <DialogHeader><DialogTitle>Title</DialogTitle></DialogHeader>
    <form onSubmit={handleSubmit} className="space-y-4">
      {/* form fields */}
      <DialogFooter>
        <Button variant="outline" onClick={() => setOpen(false)}>Cancel</Button>
        <Button type="submit" disabled={isPending}>
          {isPending && <Loader2 className="animate-spin" />} Save
        </Button>
      </DialogFooter>
    </form>
  </DialogContent>
</Dialog>
```

### Data Table (TanStack)

```tsx
const columns: ColumnDef<T>[] = [
  { accessorKey: "field", header: ({ column }) => <DataTableColumnHeader column={column} title="Field" /> },
];
const table = useReactTable({ data, columns, getCoreRowModel, getSortedRowModel, getFilteredRowModel, getPaginationRowModel });
// Render with <Table>, <TableHeader>, <TableBody>, <TableRow>, <TableCell>
// Add <DataTablePagination table={table} />
```

### Row Actions Dropdown

```tsx
<DropdownMenu>
  <DropdownMenuTrigger render={<Button variant="ghost" size="icon"><MoreHorizontal /></Button>} />
  <DropdownMenuContent align="end">
    <DropdownMenuItem onClick={onEdit}>Edit</DropdownMenuItem>
    <DropdownMenuItem variant="destructive" onClick={onDelete}>Delete</DropdownMenuItem>
  </DropdownMenuContent>
</DropdownMenu>
```

### Multi-Select

```tsx
<MultiSelectDropdown
  label="Marketplaces"
  options={MARKETPLACE_OPTIONS}
  selected={marketplaceIds}
  onChange={setMarketplaceIds}
  searchable={false}
/>
```

### Mutation with Toast

```tsx
const create = useCreateSchedule();
const onSubmit = (data: ScheduleFormData) => {
  create.mutate(data, {
    onSuccess: () => { toast.success("Created"); setOpen(false); },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Failed"),
  });
};
```
