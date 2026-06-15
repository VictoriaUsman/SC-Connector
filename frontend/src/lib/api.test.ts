import { describe, it, expect, vi, afterEach } from "vitest";
import { api } from "@/lib/api";

describe("getSpApiAuthUrl", () => {
  // Regression for CU-868jx5tv1: a client whose id contains URL-significant
  // characters (e.g. "thehome&office" for the Matini client) was passed into the
  // query string unencoded. The raw "&" prematurely terminated the client_id
  // param, so the backend only received "client_id=thehome" and returned
  // {"code":"NOT_FOUND","error":"Client not found"}.
  it("encodes a client id containing an ampersand", () => {
    const url = api.getSpApiAuthUrl("thehome&office", "na");
    expect(url).toContain("client_id=thehome%26office");
    expect(url).toContain("region=na");
    // The raw, unencoded id must never leak through — that is the original bug.
    expect(url).not.toContain("client_id=thehome&office");
  });

  it("encodes other URL-significant characters in the client id", () => {
    const url = api.getSpApiAuthUrl("a b/c#d?e", "na");
    const query = url.split("?")[1] ?? "";
    const params = new URLSearchParams(query);
    expect(params.get("client_id")).toBe("a b/c#d?e");
    expect(params.get("region")).toBe("na");
  });

  it("leaves a simple client id intact and round-trips correctly", () => {
    const url = api.getSpApiAuthUrl("acme-corp", "eu");
    const params = new URLSearchParams(url.split("?")[1] ?? "");
    expect(params.get("client_id")).toBe("acme-corp");
    expect(params.get("region")).toBe("eu");
  });
});

describe("event prior_event_id linkage", () => {
  // Regression for CU-868jx21hw: the midnight Day N recap renders YoY stats as
  // "—" whenever the live event has no prior_event_id. The create/edit event
  // payload previously dropped prior_event_id entirely (the API type only
  // allowed name/start_date/end_date), so it could never be set and YoY never
  // populated. These tests assert the field is forwarded to the backend.
  afterEach(() => {
    vi.restoreAllMocks();
  });

  function mockFetchOk() {
    return vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        new Response(JSON.stringify({ id: "e1", status: "created" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
  }

  it("sends prior_event_id when creating an event", async () => {
    const fetchSpy = mockFetchOk();

    await api.createEvent({
      name: "Prime Day 2026",
      start_date: "2026-07-13",
      end_date: "2026-07-14",
      prior_event_id: "prime_day_2025",
    });

    const [, init] = fetchSpy.mock.calls[0];
    const body = JSON.parse((init as RequestInit).body as string);
    expect(body.prior_event_id).toBe("prime_day_2025");
  });

  it("forwards prior_event_id when updating an event", async () => {
    const fetchSpy = mockFetchOk();

    await api.updateEvent("e1", { prior_event_id: "prime_day_2025" });

    const [, init] = fetchSpy.mock.calls[0];
    const body = JSON.parse((init as RequestInit).body as string);
    expect(body.prior_event_id).toBe("prime_day_2025");
  });

  it("can clear the link by sending an empty prior_event_id", async () => {
    const fetchSpy = mockFetchOk();

    await api.updateEvent("e1", { prior_event_id: "" });

    const [, init] = fetchSpy.mock.calls[0];
    const body = JSON.parse((init as RequestInit).body as string);
    expect(body.prior_event_id).toBe("");
  });
});

describe("event manual_ads (prior-year ads beyond Amazon's 95-day window)", () => {
  // CU-868jx21hw follow-up: Amazon Ads' reporting API only retains ~95 days, so
  // a year-ago prior event can't be pulled and recap YoY ads stayed 0. Operators
  // now provide those figures by hand; the payload must carry manual_ads through.
  afterEach(() => {
    vi.restoreAllMocks();
  });

  function mockFetchOk() {
    return vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        new Response(JSON.stringify({ id: "e1", status: "updated" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
  }

  it("forwards manual_ads when creating an event", async () => {
    const fetchSpy = mockFetchOk();

    await api.createEvent({
      name: "Prime Day 2025",
      start_date: "2025-07-13",
      end_date: "2025-07-14",
      manual_ads: { US: { "2025-07-13": { spend: 100.5, ppc_sales: 400 } } },
    });

    const [, init] = fetchSpy.mock.calls[0];
    const body = JSON.parse((init as RequestInit).body as string);
    expect(body.manual_ads).toEqual({
      US: { "2025-07-13": { spend: 100.5, ppc_sales: 400 } },
    });
  });

  it("forwards manual_ads when updating an event", async () => {
    const fetchSpy = mockFetchOk();

    await api.updateEvent("e1", {
      manual_ads: { CA: { "2025-07-14": { spend: 1, ppc_sales: 2 } } },
    });

    const [, init] = fetchSpy.mock.calls[0];
    const body = JSON.parse((init as RequestInit).body as string);
    expect(body.manual_ads).toEqual({
      CA: { "2025-07-14": { spend: 1, ppc_sales: 2 } },
    });
  });
});
