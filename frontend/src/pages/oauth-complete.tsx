import { useSearchParams } from "react-router-dom";
import { CheckCircle2, XCircle } from "lucide-react";

const API_SOURCE_LABELS: Record<string, string> = {
  sp_api: "SP API",
  ads_api: "Ads API",
};

export function OAuthComplete() {
  const [searchParams] = useSearchParams();
  const result = searchParams.get("oauth");
  const success = result === "success";
  const apiSource = searchParams.get("api_source") ?? "";
  const sourceLabel = API_SOURCE_LABELS[apiSource] ?? apiSource;
  const message = searchParams.get("message");

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-6">
      <div className="flex max-w-sm flex-col items-center gap-3 text-center">
        {success ? (
          <CheckCircle2 className="h-10 w-10 text-status-success" />
        ) : (
          <XCircle className="h-10 w-10 text-destructive" />
        )}
        <h1 className="text-lg font-semibold tracking-tight">
          {success
            ? `${sourceLabel || "Account"} connected successfully`
            : "Connection failed"}
        </h1>
        <p className="text-sm text-muted-foreground">
          {success
            ? "You can close this window now."
            : message || "Something went wrong. Please ask for a new connect link and try again."}
        </p>
      </div>
    </div>
  );
}
