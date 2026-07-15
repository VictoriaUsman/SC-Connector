import { describe, it, expect } from "vitest";
import { MARKETPLACES, MARKETPLACE_TO_REGION, CURRENCIES, CLIENT_TIMEZONES } from "@/types";

// AU (Amazon Australia) must be selectable end to end in the account selector
// that feeds the daily report: the marketplace dropdown, plus the AUD currency
// and Australia/Sydney timezone an AU account is configured with (CU-868k6gq75).
describe("AU marketplace support in the account selector", () => {
  it("lists AU as a selectable marketplace option", () => {
    const au = MARKETPLACES.find((m) => m.id === "AU");
    expect(au).toBeDefined();
    expect(au?.label).toBe("Australia");
  });

  it("maps AU to the Far East (fe) region", () => {
    expect(MARKETPLACE_TO_REGION["AU"]).toBe("fe");
  });

  it("offers AUD as a base currency", () => {
    expect(CURRENCIES).toContain("AUD");
  });

  it("offers Australia/Sydney as a client timezone", () => {
    expect(CLIENT_TIMEZONES.some((t) => t.value === "Australia/Sydney")).toBe(true);
  });
});
