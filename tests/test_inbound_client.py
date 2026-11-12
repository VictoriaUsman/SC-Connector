"""Tests for shared.inbound_client — FBA Inbound v2024-03-20 expiration dates."""

from __future__ import annotations

import os
import sys
from datetime import date
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))

os.environ.setdefault("GCP_PROJECT", "test-project")
os.environ.setdefault("ENVIRONMENT", "staging")

_US = "ATVPDKIKX0DER"


def _plan(plan_id: str, updated: str, *, status: str = "ACTIVE", marketplaces=None) -> dict:
    return {
        "inboundPlanId": plan_id,
        "name": f"Plan {plan_id}",
        "status": status,
        "lastUpdatedAt": updated,
        "marketplaceIds": marketplaces or [_US],
    }


def _item(msku: str, expiration: str | None = "2027-03-01") -> dict:
    item = {"msku": msku, "asin": "B0" + msku, "fnsku": "X0" + msku, "quantity": 10,
            "manufacturingLotCode": "LOT1"}
    if expiration:
        item["expiration"] = expiration
    return item


def _router(plan_pages: list[dict], items_by_plan: dict):
    """sp_api_request stand-in: serves plan list pages in order, items by plan id."""
    plan_pages = list(plan_pages)

    def _side_effect(client_id, marketplace, method, path, **kwargs):
        if path.endswith("/inboundPlans"):
            return plan_pages.pop(0)
        plan_id = path.split("/inboundPlans/")[1].split("/")[0]
        result = items_by_plan[plan_id]
        if isinstance(result, Exception):
            raise result
        return result

    return _side_effect


class TestFetchInboundExpirations:
    def test_returns_only_items_with_an_expiration(self):
        from shared import inbound_client

        side = _router(
            [{"inboundPlans": [_plan("P1", "2026-11-10T00:00:00Z")]}],
            {"P1": {"items": [_item("A"), _item("B", expiration=None)]}},
        )
        with patch.object(inbound_client, "sp_api_request", side_effect=side), \
             patch.object(inbound_client.time, "sleep"):
            rows = inbound_client.fetch_inbound_expirations("c", "US", date(2026, 11, 1))

        assert rows == [{
            "inbound_plan_id": "P1", "plan_name": "Plan P1", "plan_status": "ACTIVE",
            "plan_last_updated": "2026-11-10T00:00:00Z", "msku": "A", "asin": "B0A",
            "fnsku": "X0A", "quantity": 10, "expiration": "2027-03-01", "lot_code": "LOT1",
        }]

    def test_follows_pagination_for_plans_and_items(self):
        from shared import inbound_client

        item_pages = [
            {"items": [_item("A")], "pagination": {"nextToken": "t2"}},
            {"items": [_item("B")]},
        ]

        def _side(client_id, marketplace, method, path, **kwargs):
            if path.endswith("/inboundPlans"):
                if kwargs["params"].get("paginationToken") == "pp2":
                    return {"inboundPlans": [_plan("P2", "2026-11-09T00:00:00Z")]}
                return {"inboundPlans": [_plan("P1", "2026-11-10T00:00:00Z")],
                        "pagination": {"nextToken": "pp2"}}
            if "/P1/" in path:
                return item_pages.pop(0)
            return {"items": [_item("C")]}

        with patch.object(inbound_client, "sp_api_request", side_effect=_side), \
             patch.object(inbound_client.time, "sleep"):
            rows = inbound_client.fetch_inbound_expirations("c", "US", date(2026, 11, 1))

        assert [r["msku"] for r in rows] == ["A", "B", "C"]

    def test_stops_at_plans_older_than_start_date(self):
        from shared import inbound_client

        calls = []

        def _side(client_id, marketplace, method, path, **kwargs):
            calls.append(path)
            if path.endswith("/inboundPlans"):
                return {"inboundPlans": [_plan("NEW", "2026-11-10T00:00:00Z"),
                                         _plan("OLD", "2026-10-01T00:00:00Z")],
                        "pagination": {"nextToken": "never-followed"}}
            return {"items": [_item("A")]}

        with patch.object(inbound_client, "sp_api_request", side_effect=_side), \
             patch.object(inbound_client.time, "sleep"):
            rows = inbound_client.fetch_inbound_expirations("c", "US", date(2026, 11, 1))

        assert [r["inbound_plan_id"] for r in rows] == ["NEW"]
        assert not any("/OLD/" in p for p in calls)
        assert sum(p.endswith("/inboundPlans") for p in calls) == 1

    def test_skips_voided_plans_and_other_marketplaces(self):
        from shared import inbound_client

        side = _router(
            [{"inboundPlans": [
                _plan("VOID", "2026-11-10T00:00:00Z", status="VOIDED"),
                _plan("CA", "2026-11-10T00:00:00Z", marketplaces=["A2EUQ1WTGCTBG2"]),
                _plan("OK", "2026-11-10T00:00:00Z"),
            ]}],
            {"OK": {"items": [_item("A")]}},
        )
        with patch.object(inbound_client, "sp_api_request", side_effect=side), \
             patch.object(inbound_client.time, "sleep"):
            rows = inbound_client.fetch_inbound_expirations("c", "US", date(2026, 11, 1))

        assert [r["inbound_plan_id"] for r in rows] == ["OK"]

    def test_one_plan_failure_does_not_abort_the_rest(self):
        from shared import inbound_client
        from shared.sp_api_rest import SPAPIRequestError

        side = _router(
            [{"inboundPlans": [_plan("BAD", "2026-11-10T00:00:00Z"),
                               _plan("GOOD", "2026-11-09T00:00:00Z")]}],
            {"BAD": SPAPIRequestError(404, "Not Found"),
             "GOOD": {"items": [_item("A")]}},
        )
        with patch.object(inbound_client, "sp_api_request", side_effect=side), \
             patch.object(inbound_client.time, "sleep"):
            rows = inbound_client.fetch_inbound_expirations("c", "US", date(2026, 11, 1))

        assert [r["inbound_plan_id"] for r in rows] == ["GOOD"]

    def test_plan_list_failure_propagates(self):
        """A failure listing plans (e.g. 403 missing role) must fail the job,
        not silently produce an empty file."""
        from shared import inbound_client
        from shared.sp_api_rest import SPAPIRequestError

        with patch.object(inbound_client, "sp_api_request",
                          side_effect=SPAPIRequestError(403, "Forbidden")):
            try:
                inbound_client.fetch_inbound_expirations("c", "US", date(2026, 11, 1))
                assert False, "expected SPAPIRequestError"
            except SPAPIRequestError as exc:
                assert exc.status_code == 403
