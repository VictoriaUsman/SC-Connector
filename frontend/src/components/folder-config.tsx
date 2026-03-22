import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { FolderTree } from "lucide-react";

function PathPreview({
  folderName,
  subfolderStrategy,
}: {
  folderName: string;
  subfolderStrategy: "date" | "none";
}) {
  const root = "Google Drive";
  const folder = folderName.trim();
  const date = subfolderStrategy === "date" ? "YYYY-MM-DD" : null;

  const segments = [
    root,
    folder || null,
    date,
    "{client}",
    "{marketplace}",
    "{report_type}",
  ].filter(Boolean) as string[];

  const filename = date
    ? "{report}_{date}_{client}_{mkt}.tsv"
    : "{report}_{date}_{client}_{mkt}.tsv";

  return (
    <div className="flex items-start gap-2 rounded-md bg-muted/50 border border-dashed px-3 py-2">
      <FolderTree className="h-3.5 w-3.5 shrink-0 text-muted-foreground mt-0.5" />
      <p className="text-xs text-muted-foreground font-mono leading-relaxed break-all">
        {segments.join(" / ")} / <span className="text-foreground/70">{filename}</span>
      </p>
    </div>
  );
}

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

      <PathPreview folderName={folderName} subfolderStrategy={subfolderStrategy} />
    </div>
  );
}
