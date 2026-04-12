"""Tests for the Kalilos MCP server using FastMCP's in-process Client.

No HTTP server needed — the Client talks directly to the FastMCP instance.
API calls are mocked so tests run without a live backend.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastmcp import Client

from server import mcp


# ── Helpers ─────────────────────────────────────────────────────────────


def parse_result(result) -> dict | list:
    """Extract parsed JSON from a CallToolResult."""
    text = result.content[0].text
    return json.loads(text)


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
async def client():
    """In-process MCP client connected to the kalilos server."""
    async with Client(transport=mcp) as c:
        yield c


# ── Tool discovery ──────────────────────────────────────────────────────


EXPECTED_TOOLS = {
    "list_clients",
    "get_client",
    "list_schedules",
    "get_schedule",
    "list_jobs",
    "get_job",
    "list_report_types",
    "get_ads_report_config",
    "create_schedule",
    "update_schedule",
    "delete_schedule",
    "trigger_schedule",
    "request_report",
    "retry_job",
}


async def test_all_14_tools_registered(client: Client):
    tools = await client.list_tools()
    names = {t.name for t in tools}
    assert names == EXPECTED_TOOLS
    assert len(tools) == 14


async def test_read_tools_have_readonly_hint(client: Client):
    tools = await client.list_tools()
    read_tools = {"list_clients", "get_client", "list_schedules", "get_schedule",
                  "list_jobs", "get_job", "list_report_types", "get_ads_report_config"}
    for t in tools:
        if t.name in read_tools:
            assert t.annotations.readOnlyHint is True, f"{t.name} should be readOnly"


async def test_destructive_tool_has_hint(client: Client):
    tools = await client.list_tools()
    delete_tool = next(t for t in tools if t.name == "delete_schedule")
    assert delete_tool.annotations.destructiveHint is True


async def test_every_tool_has_description(client: Client):
    tools = await client.list_tools()
    for t in tools:
        assert t.description, f"{t.name} is missing a description"


# ── list_report_types (pure, no mock needed) ────────────────────────────


async def test_list_report_types_all(client: Client):
    result = parse_result(await client.call_tool("list_report_types", {}))
    assert "sp_api" in result
    assert "ads_api" in result
    assert "marketplaces" in result
    assert len(result["sp_api"]) == 19
    assert len(result["ads_api"]) == 9


async def test_list_report_types_sp_only(client: Client):
    result = parse_result(await client.call_tool("list_report_types", {"api_source": "sp_api"}))
    assert "sp_api" in result
    assert "ads_api" not in result


async def test_list_report_types_ads_only(client: Client):
    result = parse_result(await client.call_tool("list_report_types", {"api_source": "ads_api"}))
    assert "ads_api" in result
    assert "sp_api" not in result


# ── Tools that call the API (mocked) ────────────────────────────────────


MOCK_CLIENTS = [
    {"id": "acme", "name": "Acme Corp", "is_active": True, "marketplaces": ["US"]},
    {"id": "globex", "name": "Globex", "is_active": True, "marketplaces": ["US", "UK"]},
]


@patch("server.api.list_clients", new_callable=AsyncMock, return_value=MOCK_CLIENTS)
async def test_list_clients(mock_api, client: Client):
    data = parse_result(await client.call_tool("list_clients", {}))
    assert len(data) == 2
    assert data[0]["id"] == "acme"


@patch("server.api.get_client", new_callable=AsyncMock, return_value=MOCK_CLIENTS[0])
async def test_get_client(mock_api, client: Client):
    data = parse_result(await client.call_tool("get_client", {"client_id": "acme"}))
    assert data["name"] == "Acme Corp"


MOCK_SCHEDULES = [
    {"id": "s1", "name": "Daily SP", "is_active": True, "client_ids": ["acme"]},
]


@patch("server.api.list_schedules", new_callable=AsyncMock, return_value=MOCK_SCHEDULES)
async def test_list_schedules(mock_api, client: Client):
    data = parse_result(await client.call_tool("list_schedules", {}))
    assert data[0]["name"] == "Daily SP"


@patch("server.api.create_schedule", new_callable=AsyncMock, return_value={"id": "new-1", "status": "created"})
async def test_create_schedule(mock_api, client: Client):
    data = parse_result(await client.call_tool("create_schedule", {
        "api_source": "sp_api",
        "report_types": ["GET_FLAT_FILE_OPEN_LISTINGS_DATA"],
        "client_ids": ["acme"],
        "marketplaces": ["US"],
    }))
    assert data["status"] == "created"
    mock_api.assert_called_once()


@patch("server.api.update_schedule", new_callable=AsyncMock, return_value={"id": "s1", "status": "updated"})
async def test_update_schedule(mock_api, client: Client):
    data = parse_result(await client.call_tool("update_schedule", {
        "schedule_id": "s1", "name": "Renamed",
    }))
    assert data["status"] == "updated"
    mock_api.assert_called_once_with("s1", {"name": "Renamed"})


@patch("server.api.trigger_schedule", new_callable=AsyncMock, return_value={
    "schedule_id": "s1", "status": "triggered", "jobs_started": 2, "job_ids": ["j1", "j2"],
})
async def test_trigger_schedule(mock_api, client: Client):
    data = parse_result(await client.call_tool("trigger_schedule", {"schedule_id": "s1"}))
    assert data["jobs_started"] == 2
    assert len(data["job_ids"]) == 2


@patch("server.api.delete_schedule", new_callable=AsyncMock, return_value={"id": "s1", "status": "deleted"})
async def test_delete_schedule(mock_api, client: Client):
    data = parse_result(await client.call_tool("delete_schedule", {"schedule_id": "s1"}))
    assert data["status"] == "deleted"


MOCK_JOBS = [
    {"id": "j1", "status": "completed", "report_type": "GET_FLAT_FILE_OPEN_LISTINGS_DATA"},
    {"id": "j2", "status": "failed", "report_type": "spCampaigns", "error_details": {"message": "Throttled"}},
]


@patch("server.api.list_jobs", new_callable=AsyncMock, return_value=MOCK_JOBS)
async def test_list_jobs(mock_api, client: Client):
    data = parse_result(await client.call_tool("list_jobs", {"limit": 10}))
    assert len(data) == 2


@patch("server.api.retry_job", new_callable=AsyncMock, return_value={
    "status": "retried", "original_job_id": "j2", "new_job_id": "j3",
})
async def test_retry_job(mock_api, client: Client):
    data = parse_result(await client.call_tool("retry_job", {"job_id": "j2"}))
    assert data["status"] == "retried"
    assert data["new_job_id"] == "j3"


@patch("server.api.request_on_demand", new_callable=AsyncMock, return_value={
    "job_ids": ["j10"], "jobs_started": 1, "errors": 0, "status": "started",
})
async def test_request_report(mock_api, client: Client):
    data = parse_result(await client.call_tool("request_report", {
        "client_id": "acme",
        "api_source": "sp_api",
        "marketplace": "US",
        "report_types": ["GET_FLAT_FILE_OPEN_LISTINGS_DATA"],
        "start_date": "2026-04-01",
        "end_date": "2026-04-10",
    }))
    assert data["jobs_started"] == 1


# ── Error handling ──────────────────────────────────────────────────────


@patch("server.api.get_client", new_callable=AsyncMock)
async def test_api_error_returns_structured_error(mock_api, client: Client):
    from api_client import ApiError
    mock_api.side_effect = ApiError(404, "NOT_FOUND", "Client not found")
    data = parse_result(await client.call_tool("get_client", {"client_id": "nope"}))
    assert data["error"] is True
    assert data["code"] == "NOT_FOUND"


async def test_update_schedule_no_fields_returns_error(client: Client):
    data = parse_result(await client.call_tool("update_schedule", {"schedule_id": "s1"}))
    assert data["error"] is True
    assert "No fields" in data["message"]
