import { useState, useRef, useMemo, useEffect } from "react";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import { ChevronDown, Search, X } from "lucide-react";

export interface DropdownOption {
  id: string;
  label: string;
}

export function MultiSelectDropdown<T extends DropdownOption>({
  label,
  placeholder,
  options,
  selected,
  onChange,
  renderOption,
  renderBadge,
  maxBadges = 3,
  searchable = true,
}: {
  label: string;
  placeholder?: string;
  options: T[];
  selected: string[];
  onChange: (selected: string[]) => void;
  renderOption?: (item: T) => React.ReactNode;
  renderBadge?: (item: T) => React.ReactNode;
  maxBadges?: number;
  searchable?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setSearch("");
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  const filtered = useMemo(() => {
    if (!search) return options;
    const q = search.toLowerCase();
    return options.filter(
      (o) =>
        o.label.toLowerCase().includes(q) ||
        o.id.toLowerCase().includes(q),
    );
  }, [options, search]);

  const selectedItems = useMemo(
    () => options.filter((o) => selected.includes(o.id)),
    [options, selected],
  );

  const toggle = (id: string) => {
    onChange(
      selected.includes(id)
        ? selected.filter((s) => s !== id)
        : [...selected, id],
    );
  };

  const toggleAll = () => {
    if (selected.length === options.length) {
      onChange([]);
    } else {
      onChange(options.map((o) => o.id));
    }
  };

  const removeItem = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    onChange(selected.filter((s) => s !== id));
  };

  const visibleBadges = selectedItems.slice(0, maxBadges);
  const overflowCount = selectedItems.length - maxBadges;

  return (
    <div className="space-y-2">
      <Label>{label}</Label>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger
          render={
            <button
              type="button"
              className={cn(
                "flex w-full min-h-9 items-center justify-between rounded-md border border-input bg-transparent px-3 py-1.5 text-sm transition-colors",
                "hover:border-muted-foreground/30 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
                !selected.length && "text-muted-foreground",
              )}
            >
              <div className="flex flex-1 flex-wrap gap-1 items-center">
                {selected.length === 0 && (
                  <span>{placeholder ?? `Select ${label.toLowerCase()}...`}</span>
                )}
                {visibleBadges.map((item) => (
                  <Badge
                    key={item.id}
                    variant="secondary"
                    className="gap-1 px-1.5 py-0 text-xs font-normal"
                  >
                    {renderBadge ? renderBadge(item) : item.label}
                    <span
                      role="button"
                      tabIndex={-1}
                      onPointerDown={(e) => removeItem(item.id, e)}
                      className="ml-0.5 rounded-full hover:bg-muted-foreground/20 p-0.5 cursor-pointer"
                    >
                      <X className="h-2.5 w-2.5" />
                    </span>
                  </Badge>
                ))}
                {overflowCount > 0 && (
                  <Badge variant="secondary" className="px-1.5 py-0 text-xs font-normal">
                    +{overflowCount} more
                  </Badge>
                )}
              </div>
              <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground ml-2" />
            </button>
          }
        />
        <PopoverContent
          align="start"
          className="w-[var(--anchor-width)] p-0"
        >
          {searchable && options.length > 5 && (
            <div className="flex items-center gap-2 border-b px-3 py-2">
              <Search className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
              <input
                ref={inputRef}
                type="text"
                placeholder="Search..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
              />
              {search && (
                <button
                  type="button"
                  onClick={() => setSearch("")}
                  className="text-muted-foreground hover:text-foreground"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
          )}

          <div className="flex items-center justify-between px-3 py-1.5 border-b">
            <span className="text-xs text-muted-foreground">
              {selected.length} of {options.length} selected
            </span>
            <button
              type="button"
              onClick={toggleAll}
              className="text-xs text-muted-foreground hover:text-foreground transition-colors"
            >
              {selected.length === options.length ? "Clear all" : "Select all"}
            </button>
          </div>

          <div className="max-h-56 overflow-y-auto p-1">
            {filtered.length === 0 ? (
              <p className="py-4 text-center text-xs text-muted-foreground">
                No results found
              </p>
            ) : (
              filtered.map((item) => (
                <label
                  key={item.id}
                  className="flex items-center gap-2.5 cursor-pointer rounded-md px-2 py-1.5 hover:bg-accent text-sm"
                >
                  <Checkbox
                    checked={selected.includes(item.id)}
                    onCheckedChange={() => toggle(item.id)}
                  />
                  {renderOption ? renderOption(item) : item.label}
                </label>
              ))
            )}
          </div>
        </PopoverContent>
      </Popover>
    </div>
  );
}
