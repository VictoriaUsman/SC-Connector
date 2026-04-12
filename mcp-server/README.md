# Kalilos MCP Server

Remote MCP server that gives Claude agents full access to the Kalilos Amazon Reports Connector. Built with [FastMCP](https://gofastmcp.com/) 3.x.

Works across **all Claude surfaces**: Cursor, Claude Code, Claude Desktop, Claude.ai, and the Anthropic API.

## Tools (14)

| Tool | Hints | Description |
|------|-------|-------------|
| `list_clients` | readOnly | List Amazon seller clients |
| `get_client` | readOnly | Get client details and connection status |
| `list_schedules` | readOnly | List report schedules (filterable) |
| `get_schedule` | readOnly | Get full schedule configuration |
| `list_jobs` | readOnly | List jobs by status, client, date |
| `get_job` | readOnly | Get job details including Drive path |
| `list_report_types` | readOnly | All SP + Ads report types with constraints |
| `get_ads_report_config` | readOnly | Default columns for an Ads report |
| `create_schedule` | write | Create a recurring report schedule |
| `update_schedule` | write, idempotent | Partial-update a schedule |
| `delete_schedule` | destructive | Delete a schedule permanently |
| `trigger_schedule` | write, openWorld | Run Now — trigger all schedule jobs |
| `request_report` | write, openWorld | On-demand report for a date range |
| `retry_job` | write, openWorld | Retry a failed job |

## Quick Start

```bash
cd mcp-server
python3 -m venv .venv && source .venv/bin/activate
pip install ".[test]"

# Run tests (19 tests, <1s, no backend needed)
pytest -v

# Start locally (HTTP)
KALILOS_API_URL=https://...cloudfunctions.net/kalilos-staging-api python server.py

# Start locally (stdio, for Cursor/Claude Code)
KALILOS_API_URL=https://...cloudfunctions.net/kalilos-staging-api MCP_TRANSPORT=stdio python server.py
```

Or via Makefile from the project root:

```bash
make local-mcp          # stdio mode
make local-mcp-http     # HTTP mode on port 8081
```

## Testing

Tests use FastMCP's in-process `Client(transport=mcp)` pattern — no HTTP server needed. API calls are mocked.

```bash
cd mcp-server && source .venv/bin/activate
pytest tests/ -v
```

Tests cover:
- All 14 tools registered with correct names
- ToolAnnotations (readOnlyHint, destructiveHint) set correctly
- Every tool has a description
- Report type catalog returns correct counts
- Each tool returns expected data (mocked API)
- API errors return structured `{error, code, message}` dicts
- Edge cases (update with no fields)

## Deployment (Cloud Run)

### 1. Set secrets

```bash
# MCP bearer token (for Claude clients)
make secret-set NAME=kalilos-staging-mcp-api-key VALUE=$(openssl rand -hex 32)

# REST API key (for MCP server -> API calls)
make secret-set NAME=kalilos-staging-api-key VALUE=$(openssl rand -hex 32)

# Set API key in Pulumi config (for the API Cloud Function)
cd infra && pulumi config set --secret kalilos:api-key <api-key> --stack staging
```

### 2. Deploy

```bash
make env-staging && make deploy-mcp
```

### 3. Get the URL

```bash
cd infra && pulumi stack output mcp_server_url --stack staging
```

## Connecting Clients

### Cursor IDE

Set in your shell profile or `.env`:

```bash
export KALILOS_MCP_URL=https://kalilos-staging-mcp-xxxx.run.app/mcp
export KALILOS_MCP_API_KEY=your-mcp-api-key
```

The project `.mcp.json` reads these env vars automatically.

### Claude Code

```bash
claude mcp add kalilos \
  --transport http \
  --url https://kalilos-staging-mcp-xxxx.run.app/mcp \
  --header "Authorization: Bearer $KALILOS_MCP_API_KEY"
```

### Claude Desktop / Claude.ai

Settings > Connectors > Add Connector > enter the Cloud Run URL and API key.

### Anthropic API

```python
response = client.beta.messages.create(
    model="claude-sonnet-4-20250514",
    max_tokens=4096,
    messages=[{"role": "user", "content": "List all active schedules"}],
    mcp_servers=[{
        "type": "url",
        "url": "https://kalilos-prod-mcp-xxxx.run.app/mcp",
        "name": "kalilos",
        "authorization_token": "your-mcp-api-key",
    }],
    tools=[{"type": "mcp_toolset", "mcp_server_name": "kalilos"}],
    betas=["mcp-client-2025-11-20"],
)
```

## Architecture

```
Claude Client  -->  MCP Server (Cloud Run)  -->  REST API (Cloud Function)  -->  Firestore / Workflows / Drive
                    FastMCP + Bearer auth        X-API-Key auth
```

The MCP server is a thin proxy — all business logic lives in the REST API. Changes to the API automatically flow through.

## Auth

| Layer | Mechanism | Secret |
|-------|-----------|--------|
| MCP Server | Bearer token in `Authorization` header | `MCP_API_KEY` env var |
| REST API | `X-API-Key` header | `API_KEY` env var |

Both layers are disabled when their env var is unset (local dev).

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `KALILOS_API_URL` | Yes | REST API base URL |
| `KALILOS_API_KEY` | Prod | API key for REST API calls |
| `MCP_API_KEY` | Prod | Bearer token for MCP auth |
| `MCP_TRANSPORT` | No | `streamable-http` (default) or `stdio` |
| `PORT` | No | HTTP port (default 8080) |
