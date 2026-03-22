import type { Column } from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface DataTableColumnHeaderProps<TData, TValue>
  extends React.HTMLAttributes<HTMLDivElement> {
  column: Column<TData, TValue>;
  title: string;
}

export function DataTableColumnHeader<TData, TValue>({
  column,
  title,
  className,
}: DataTableColumnHeaderProps<TData, TValue>) {
  if (!column.getCanSort()) {
    return <div className={cn(className)}>{title}</div>;
  }

  const sorted = column.getIsSorted();

  return (
    <Button
      variant="ghost"
      size="sm"
      className={cn("-ml-2 h-8 font-medium", className)}
      onClick={() => column.toggleSorting(sorted === "asc")}
    >
      {title}
      {sorted === "desc" ? (
        <ArrowDown className="ml-1 h-3.5 w-3.5" />
      ) : sorted === "asc" ? (
        <ArrowUp className="ml-1 h-3.5 w-3.5" />
      ) : (
        <ChevronsUpDown className="ml-1 h-3.5 w-3.5 text-muted-foreground/50" />
      )}
    </Button>
  );
}
