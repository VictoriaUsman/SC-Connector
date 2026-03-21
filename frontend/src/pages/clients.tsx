import { useState, useEffect } from "react";
import { useSearchParams } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { EmptyState } from "@/components/empty-state";
import {
  useClients,
  useCreateClient,
  useUpdateClient,
  useDeleteClient,
} from "@/hooks/use-clients";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { MARKETPLACES } from "@/types";
import type { Client } from "@/types";
import { Textarea } from "@/components/ui/textarea";
import {
  Loader2,
  MoreHorizontal,
  Plus,
  Users,
  Pencil,
  Trash2,
  Link2,
  Key,
  CheckCircle2,
  Circle,
} from "lucide-react";
import { toast } from "sonner";

function ConnectionStatus({ connected, label }: { connected: boolean; label: string }) {
  return (
    <span className="inline-flex items-center gap-1 text-xs">
      {connected ? (
        <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
      ) : (
        <Circle className="h-3.5 w-3.5 text-muted-foreground/40" />
      )}
      <span className={connected ? "text-emerald-600 dark:text-emerald-400" : "text-muted-foreground"}>
        {label}
      </span>
    </span>
  );
}

function ClientForm({
  initial,
  onSubmit,
  onCancel,
  isPending,
}: {
  initial?: Client;
  onSubmit: (data: { id: string; name: string; marketplaces: string[] }) => void;
  onCancel: () => void;
  isPending: boolean;
}) {
  const [id, setId] = useState(initial?.id ?? "");
  const [name, setName] = useState(initial?.name ?? "");
  const [selected, setSelected] = useState<Set<string>>(
    new Set(initial?.marketplaces ?? []),
  );

  const toggle = (mkt: string) => {
    const next = new Set(selected);
    if (next.has(mkt)) next.delete(mkt);
    else next.add(mkt);
    setSelected(next);
  };

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit({ id, name, marketplaces: [...selected] });
      }}
      className="space-y-4"
    >
      <div className="space-y-2">
        <Label htmlFor="client-id">Client ID</Label>
        <Input
          id="client-id"
          value={id}
          onChange={(e) => setId(e.target.value)}
          placeholder="acme-corp"
          disabled={!!initial}
          required
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="client-name">Name</Label>
        <Input
          id="client-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Acme Corporation"
          required
        />
      </div>
      <div className="space-y-2">
        <Label>Marketplaces</Label>
        <div className="flex flex-wrap gap-2">
          {MARKETPLACES.map((mkt) => (
            <button
              key={mkt.id}
              type="button"
              onClick={() => toggle(mkt.id)}
              className={`rounded-md border px-2.5 py-1 text-xs font-medium transition-colors ${
                selected.has(mkt.id)
                  ? "border-primary bg-primary/10 text-primary"
                  : "border-border text-muted-foreground hover:border-primary/50"
              }`}
            >
              {mkt.flag} {mkt.id}
            </button>
          ))}
        </div>
      </div>
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" disabled={isPending || !id || !name}>
          {isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          {initial ? "Update" : "Create"}
        </Button>
      </DialogFooter>
    </form>
  );
}

export function Clients() {
  const { data: clients, isLoading } = useClients();
  const createClient = useCreateClient();
  const updateClient = useUpdateClient();
  const deleteClient = useDeleteClient();
  const [editingClient, setEditingClient] = useState<Client | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [connectOpen, setConnectOpen] = useState(false);
  const [connectTarget, setConnectTarget] = useState<{ client: Client; apiSource: "sp_api" | "ads_api" } | null>(null);
  const [connectToken, setConnectToken] = useState("");
  const [connectLoading, setConnectLoading] = useState(false);
  const [searchParams, setSearchParams] = useSearchParams();

  useEffect(() => {
    const oauthResult = searchParams.get("oauth");
    if (oauthResult === "success") {
      const source = searchParams.get("api_source");
      toast.success(`${source === "sp_api" ? "SP API" : "Ads API"} connected successfully`);
      setSearchParams({}, { replace: true });
    } else if (oauthResult === "error") {
      const message = searchParams.get("message") ?? "Unknown error";
      toast.error(`OAuth failed: ${message}`);
      setSearchParams({}, { replace: true });
    }
  }, [searchParams, setSearchParams]);

  const handleCreate = (data: { id: string; name: string; marketplaces: string[] }) => {
    createClient.mutate(data, {
      onSuccess: () => {
        setCreateOpen(false);
        toast.success("Client created");
      },
      onError: (err) => toast.error(err.message),
    });
  };

  const handleUpdate = (data: { id: string; name: string; marketplaces: string[] }) => {
    updateClient.mutate(data, {
      onSuccess: () => {
        setEditOpen(false);
        setEditingClient(null);
        toast.success("Client updated");
      },
      onError: (err) => toast.error(err.message),
    });
  };

  const handleToggleActive = (client: Client) => {
    updateClient.mutate(
      { id: client.id, is_active: !client.is_active },
      {
        onSuccess: () =>
          toast.success(`${client.name} ${client.is_active ? "deactivated" : "activated"}`),
        onError: (err) => toast.error(err.message),
      },
    );
  };

  const handleDelete = (client: Client) => {
    if (!confirm(`Delete client "${client.name}"? This cannot be undone.`)) return;
    deleteClient.mutate(client.id, {
      onSuccess: () => toast.success("Client deleted"),
      onError: (err) => toast.error(err.message),
    });
  };

  const openManualConnect = (client: Client, apiSource: "sp_api" | "ads_api") => {
    setConnectTarget({ client, apiSource });
    setConnectToken("");
    setConnectOpen(true);
  };

  const handleManualConnect = async () => {
    if (!connectTarget || !connectToken.trim()) return;
    setConnectLoading(true);
    try {
      await api.connectManual(connectTarget.client.id, {
        api_source: connectTarget.apiSource,
        refresh_token: connectToken.trim(),
      });
      toast.success(`${connectTarget.apiSource === "sp_api" ? "SP API" : "Ads API"} connected successfully`);
      setConnectOpen(false);
      setConnectTarget(null);
      setConnectToken("");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Connection failed");
    } finally {
      setConnectLoading(false);
    }
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">Clients</h1>
        <Dialog open={createOpen} onOpenChange={setCreateOpen}>
          <Button onClick={() => setCreateOpen(true)}>
            <Plus className="mr-2 h-4 w-4" />
            Add Client
          </Button>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>New Client</DialogTitle>
            </DialogHeader>
            <ClientForm
              onSubmit={handleCreate}
              onCancel={() => setCreateOpen(false)}
              isPending={createClient.isPending}
            />
          </DialogContent>
        </Dialog>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>All Clients</CardTitle>
        </CardHeader>
        <CardContent>
          {!clients?.length ? (
            <EmptyState
              icon={Users}
              title="No clients yet"
              description="Add your first client to start configuring report schedules."
              action={
                <Button variant="outline" onClick={() => setCreateOpen(true)}>
                  <Plus className="mr-2 h-4 w-4" />
                  Add Client
                </Button>
              }
            />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>ID</TableHead>
                  <TableHead>Marketplaces</TableHead>
                  <TableHead>Connections</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead className="w-10" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {clients.map((client) => (
                  <TableRow key={client.id}>
                    <TableCell className="font-medium">{client.name}</TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      {client.id}
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        {client.marketplaces?.map((m) => (
                          <Badge key={m} variant="secondary" className="text-xs">
                            {m}
                          </Badge>
                        ))}
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-col gap-1">
                        <ConnectionStatus
                          connected={!!client.sp_api_secret_name}
                          label="SP API"
                        />
                        <ConnectionStatus
                          connected={!!client.ads_api_secret_name}
                          label="Ads API"
                        />
                      </div>
                    </TableCell>
                    <TableCell>
                      <Switch
                        checked={client.is_active}
                        onCheckedChange={() => handleToggleActive(client)}
                      />
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {formatDate(client.created_at)}
                    </TableCell>
                    <TableCell>
                      <DropdownMenu>
                        <DropdownMenuTrigger
                          render={
                            <Button variant="ghost" size="icon" className="h-8 w-8">
                              <MoreHorizontal className="h-4 w-4" />
                            </Button>
                          }
                        />
                        <DropdownMenuContent align="end">
                          {!client.sp_api_secret_name && (
                            <>
                              <DropdownMenuItem
                                onClick={() => { window.location.href = api.getSpApiAuthUrl(client.id); }}
                              >
                                <Link2 className="mr-2 h-4 w-4" />
                                Connect SP API (OAuth)
                              </DropdownMenuItem>
                              <DropdownMenuItem
                                onClick={() => openManualConnect(client, "sp_api")}
                              >
                                <Key className="mr-2 h-4 w-4" />
                                Connect SP API (Token)
                              </DropdownMenuItem>
                            </>
                          )}
                          {!client.ads_api_secret_name && (
                            <>
                              <DropdownMenuItem
                                onClick={() => { window.location.href = api.getAdsApiAuthUrl(client.id); }}
                              >
                                <Link2 className="mr-2 h-4 w-4" />
                                Connect Ads API (OAuth)
                              </DropdownMenuItem>
                              <DropdownMenuItem
                                onClick={() => openManualConnect(client, "ads_api")}
                              >
                                <Key className="mr-2 h-4 w-4" />
                                Connect Ads API (Token)
                              </DropdownMenuItem>
                            </>
                          )}
                          <DropdownMenuItem
                            onClick={() => {
                              setEditingClient(client);
                              setEditOpen(true);
                            }}
                          >
                            <Pencil className="mr-2 h-4 w-4" />
                            Edit
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            variant="destructive"
                            onClick={() => handleDelete(client)}
                          >
                            <Trash2 className="mr-2 h-4 w-4" />
                            Delete
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Dialog open={editOpen} onOpenChange={(open) => { setEditOpen(open); if (!open) setEditingClient(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Edit Client</DialogTitle>
          </DialogHeader>
          {editingClient && (
            <ClientForm
              initial={editingClient}
              onSubmit={handleUpdate}
              onCancel={() => { setEditOpen(false); setEditingClient(null); }}
              isPending={updateClient.isPending}
            />
          )}
        </DialogContent>
      </Dialog>

      <Dialog open={connectOpen} onOpenChange={(open) => { setConnectOpen(open); if (!open) setConnectTarget(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              Connect {connectTarget?.apiSource === "sp_api" ? "SP API" : "Ads API"} — {connectTarget?.client.name}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="refresh-token">Refresh Token</Label>
              <Textarea
                id="refresh-token"
                value={connectToken}
                onChange={(e) => setConnectToken(e.target.value)}
                placeholder="Atzr|IwEBxxxxxxx..."
                rows={4}
                className="font-mono text-xs"
              />
              <p className="text-xs text-muted-foreground">
                Paste the Amazon refresh token for this seller account. This will be stored securely in Secret Manager.
              </p>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => { setConnectOpen(false); setConnectTarget(null); }}>
                Cancel
              </Button>
              <Button onClick={handleManualConnect} disabled={connectLoading || !connectToken.trim()}>
                {connectLoading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                Connect
              </Button>
            </DialogFooter>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
