import { describe, it, expect } from "vitest";
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
