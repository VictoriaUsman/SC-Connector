import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function FolderConfig({
  folderName,
  onFolderNameChange,
  subfolderStrategy,
  onSubfolderStrategyChange,
}: {
  folderName: string;
  onFolderNameChange: (value: string) => void;
  subfolderStrategy: "date" | "none";
  onSubfolderStrategyChange: (value: "date" | "none") => void;
}) {
  return (
    <div className="space-y-3">
      <div className="space-y-2">
        <Label>Save to folder (optional)</Label>
        <Input
          placeholder="e.g. WoW Weekly Reports"
          value={folderName}
          onChange={(e) => onFolderNameChange(e.target.value)}
        />
        <p className="text-xs text-muted-foreground">
          Leave empty to use the default layout: date / client / marketplace / report type
        </p>
      </div>

      <div className="space-y-2">
        <Label>Subfolder strategy</Label>
        <div className="flex gap-4">
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input
              type="radio"
              name="subfolder"
              checked={subfolderStrategy === "date"}
              onChange={() => onSubfolderStrategyChange("date")}
              className="accent-primary"
            />
            Create date-based subfolders
          </label>
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input
              type="radio"
              name="subfolder"
              checked={subfolderStrategy === "none"}
              onChange={() => onSubfolderStrategyChange("none")}
              className="accent-primary"
            />
            Save all in the same folder
          </label>
        </div>
      </div>
    </div>
  );
}
