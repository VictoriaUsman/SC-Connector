import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";

export interface CheckboxOption {
  id: string;
  label: string;
}

export function MultiCheckboxSelect<T extends CheckboxOption>({
  label,
  options,
  selected,
  onChange,
  renderItem,
}: {
  label: string;
  options: T[];
  selected: string[];
  onChange: (selected: string[]) => void;
  renderItem?: (item: T) => React.ReactNode;
}) {
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

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <Label>{label}</Label>
        <button
          type="button"
          onClick={toggleAll}
          className="text-xs text-muted-foreground hover:text-foreground transition-colors"
        >
          {selected.length === options.length ? "Clear all" : "Select all"}
        </button>
      </div>
      <div className="grid grid-cols-2 gap-1.5 rounded-md border p-3 max-h-48 overflow-y-auto">
        {options.map((item) => (
          <label
            key={item.id}
            className="flex items-center gap-2 cursor-pointer rounded px-1.5 py-1 hover:bg-accent text-sm"
          >
            <Checkbox
              checked={selected.includes(item.id)}
              onCheckedChange={() => toggle(item.id)}
            />
            {renderItem ? renderItem(item) : item.label}
          </label>
        ))}
      </div>
      {selected.length > 0 && (
        <p className="text-xs text-muted-foreground">
          {selected.length} selected
        </p>
      )}
    </div>
  );
}
