"""Typed async HTTP client wrapping the Kalilos REST API."""

from __future__ import annotations

import os
from typing import Any

import httpx


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        super().__init__(message)

    def __str__(self) -> str:
        return f"[{self.code}] {super().__str__()} (HTTP {self.status})"


class KalilosApiClient:
    """Async HTTP client for the Kalilos REST API.

    All methods return parsed JSON dicts/lists. Raises ``ApiError`` on
    non-2xx responses with the structured error payload from the API.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = (base_url or os.environ.get("KALILOS_API_URL", "")).rstrip("/")
        self._api_key = api_key or os.environ.get("KALILOS_API_KEY", "")
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if self._api_key:
                headers["X-API-Key"] = self._api_key
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers=headers,
                timeout=self._timeout,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        client = await self._get_client()
        resp = await client.request(method, path, **kwargs)
        data = resp.json()
        if resp.status_code >= 400:
            raise ApiError(
                status=resp.status_code,
                code=data.get("code", "UNKNOWN"),
                message=data.get("error", resp.reason_phrase or "Request failed"),
            )
        return data

    # --------------------------------------------------------------------- #
    # Clients
    # --------------------------------------------------------------------- #

    async def list_clients(self, *, active: bool = False) -> list[dict]:
        params: dict[str, str] = {}
        if active:
            params["active"] = "true"
        return await self._request("GET", "/clients", params=params)

    async def get_client(self, client_id: str) -> dict:
        return await self._request("GET", f"/clients/{client_id}")

    # --------------------------------------------------------------------- #
    # Schedules
    # --------------------------------------------------------------------- #

    async def list_schedules(
        self,
        *,
        client_id: str | None = None,
        active: bool = False,
    ) -> list[dict]:
        params: dict[str, str] = {}
        if client_id:
            params["client_id"] = client_id
        if active:
            params["active"] = "true"
        return await self._request("GET", "/schedules", params=params)

    async def get_schedule(self, schedule_id: str) -> dict:
        return await self._request("GET", f"/schedules/{schedule_id}")

    async def create_schedule(self, data: dict) -> dict:
        return await self._request("POST", "/schedules", json=data)

    async def update_schedule(self, schedule_id: str, data: dict) -> dict:
        return await self._request("PUT", f"/schedules/{schedule_id}", json=data)

    async def delete_schedule(self, schedule_id: str) -> dict:
        return await self._request("DELETE", f"/schedules/{schedule_id}")

    async def trigger_schedule(self, schedule_id: str) -> dict:
        return await self._request("POST", f"/schedules/{schedule_id}/trigger")

    # --------------------------------------------------------------------- #
    # Jobs
    # --------------------------------------------------------------------- #

    async def list_jobs(
        self,
        *,
        client_id: str | None = None,
        status: str | None = None,
        schedule_id: str | None = None,
        execution_date: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        params: dict[str, str] = {"limit": str(min(limit, 200))}
        if client_id:
            params["client_id"] = client_id
        if status:
            params["status"] = status
        if schedule_id:
            params["schedule_id"] = schedule_id
        if execution_date:
            params["execution_date"] = execution_date
        return await self._request("GET", "/jobs", params=params)

    async def get_job(self, job_id: str) -> dict:
        return await self._request("GET", f"/jobs/{job_id}")

    async def retry_job(self, job_id: str) -> dict:
        return await self._request("POST", f"/jobs/{job_id}/retry")

    # --------------------------------------------------------------------- #
    # On-demand
    # --------------------------------------------------------------------- #

    async def request_on_demand(self, data: dict) -> dict:
        return await self._request("POST", "/on-demand", json=data)

    # --------------------------------------------------------------------- #
    # Ads report config
    # --------------------------------------------------------------------- #

    async def list_ads_report_config(self) -> dict:
        return await self._request("GET", "/ads-report-config")

    async def get_ads_report_config(self, report_type: str) -> dict:
        return await self._request("GET", f"/ads-report-config/{report_type}")
