"""Authentication for the Kalilos MCP server.

MCP-level auth is intentionally disabled. The MCP server is a thin proxy
that delegates all operations to the REST API, which enforces its own
API key authentication (X-API-Key header). FastMCP's StaticTokenVerifier
uses an OAuth2 dynamic-client-registration flow that Claude.ai connectors
do not support, causing "not connected" errors.

Security layers:
  1. REST API requires X-API-Key on every request (Cloud Function)
  2. KALILOS_API_KEY env var is only available inside the Cloud Run container
  3. Cloud Run service is publicly accessible (allUsers invoker) so Claude.ai
     and other MCP clients can reach it without GCP IAM tokens
"""

from __future__ import annotations


def create_auth_provider() -> None:
    """Return None — MCP-level auth is disabled; REST API handles authz."""
    return None
