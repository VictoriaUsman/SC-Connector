"""Bearer token authentication for the Kalilos MCP server.

Uses FastMCP's StaticTokenVerifier for API key validation.
The key is loaded from the MCP_API_KEY environment variable
(sourced from GCP Secret Manager in production).

When MCP_API_KEY is unset, authentication is disabled (local dev).
"""

from __future__ import annotations

import os

from fastmcp.server.auth.providers.jwt import StaticTokenVerifier


def create_auth_provider() -> StaticTokenVerifier | None:
    """Return an auth provider if MCP_API_KEY is set, else None (no auth)."""
    api_key = os.environ.get("MCP_API_KEY", "").strip()
    if not api_key:
        return None

    return StaticTokenVerifier(
        tokens={api_key: {"client_id": "kalilos-agent"}},
    )
