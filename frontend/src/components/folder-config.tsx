import { useState } from "react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { FolderTree, AlertCircle } from "lucide-react";

function PathPreview({
  folderName,
  subfolderStrategy,
}: {
  folderName: string;
  subfolderStrategy: "date" | "none";
}) {
  const root = "Google Drive";
  const folderParts = folderName
    .split("/")
    .map((s) => s.trim())
    .filter(Boolean);
  const date = subfolderStrategy === "date" ? "YYYY-MM-DD" : null;

  const segments = [
    root,
    ...folderParts,
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

const INVALID_FOLDER_CHARS = /\\/g;

function sanitizeFolderName(raw: string): string {
  return raw.replace(INVALID_FOLDER_CHARS, "/");
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
  const [showHint, setShowHint] = useState(false);

  const handleFolderChange = (raw: string) => {
    const sanitized = sanitizeFolderName(raw);
    if (sanitized !== raw) {
      setShowHint(true);
      setTimeout(() => setShowHint(false), 3000);
    }
    onFolderNameChange(sanitized);
  };

  const handleFolderBlur = () => {
    // Trim each segment so a stray leading/trailing space (e.g. "MTD Ads KPIs ")
    // can't create a second, visually-identical Drive folder. The preview already
    // trims for display, so this makes the stored value match what the user sees.
    const trimmed = folderName
      .split("/")
      .map((s) => s.trim())
      .filter(Boolean)
      .join("/");
    if (trimmed !== folderName) {
      onFolderNameChange(trimmed);
    }
  };

  const hasNestedFolders = folderName.includes("/");

  return (
    <div className="space-y-3">
      <div className="space-y-2">
        <Label>Save to folder (optional)</Label>
        <Input
          placeholder="e.g. WoW Weekly Reports"
          value={folderName}
          onChange={(e) => handleFolderChange(e.target.value)}
          onBlur={handleFolderBlur}
        />
        {showHint ? (
          <p className="flex items-center gap-1.5 text-xs text-amber-600 dark:text-amber-400">
            <AlertCircle className="h-3 w-3 shrink-0" />
            Backslashes are converted to forward slashes
          </p>
        ) : hasNestedFolders ? (
          <p className="text-xs text-muted-foreground">
            Slashes create nested sub-folders in Google Drive
          </p>
        ) : (
          <p className="text-xs text-muted-foreground">
            Leave empty for default layout. Use / to create nested folders.
          </p>
        )}
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
