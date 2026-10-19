import { describe, expect, it } from "vitest";
import { parsePlanChecks, parsePlanCheckReceipt } from "../lib/management-checks";
import { parseActivePlanResponse } from "../lib/training-management";

const id = "f7dfdc52-69a8-4a01-890d-36bfe2fb00b7";
const timestamp = "2026-10-19T09:42:13.123456+00:00";
const check = { id, sequence: 13, checked_revision: 6, operation: "CHECK_NO_CHANGE", recorded_at: timestamp, reason: "Проверката приключи. Програмата остава без промяна." };
const receipt = { operation: "CHECK_NO_CHANGE", check_id: id, checked_revision: 6, recorded_at: timestamp };
const draft = { schema_version: "planning-draft-v1", engine_version: "training-management-v50", status: "DRAFT", start_date: "2026-10-19", end_date: "2026-10-25", days: [], source: {}, parameters: {}, warnings: [] };
const active = { revision: 6, stale: false, actionable: false, payload: { schema_version: "active-plan-v2", status: "REVIEW_REQUIRED", mode: "REVIEW", reason: "За преглед", plan: draft, proposal: null, changes: [], outcomes: [], decisions: {} } };

describe("separate immutable plan checks", () => {
  it("keeps check identities and the next page separate from plan revisions", () => {
    const value = { checks: [check, { ...check, id: "932241b6-d9d6-4929-9fd6-dea109f567f0", sequence: 11 }], next_before: 11 };
    expect(parsePlanChecks(value)).toEqual(value);
    expect(parsePlanChecks({ checks: [], next_before: null })).toEqual({ checks: [], next_before: null });
  });
  it.each([
    { checks: null, next_before: null },
    { checks: [check] },
    { checks: [check], next_before: 12 },
    { checks: [], next_before: 13 },
    { checks: [check, check], next_before: null },
    { checks: [check, { ...check, id: id.toUpperCase(), sequence: 11 }], next_before: null },
    { checks: [{ ...check, sequence: 0 }], next_before: null },
    { checks: [{ ...check, checked_revision: 6.5 }], next_before: null },
    { checks: [{ ...check, id: "version-6" }], next_before: null },
    { checks: [{ ...check, recorded_at: "2026-10-19T09:42:13" }], next_before: null },
    { checks: [{ ...check, operation: "REFRESH" }], next_before: null },
    { checks: [{ ...check, reason: "  " }], next_before: null },
    { checks: [{ ...check, reason: "x".repeat(501) }], next_before: null },
  ])("rejects malformed or ambiguous check history %#", value => expect(() => parsePlanChecks(value)).toThrow());
  it("supports an old API without inventing checks capability or receipt", () => {
    const value = { active, history: [] };
    expect(parseActivePlanResponse(value)).toEqual(value);
    expect(parseActivePlanResponse(value).capabilities).toBeUndefined();
    expect(parseActivePlanResponse({ active: null, history: [], capabilities: { unchanged_checks: true } }).capabilities?.unchanged_checks).toBe(true);
  });
  it("accepts a receipt for a checked version while a later plan is current", () => {
    const value = { active: { ...active, revision: 7 }, history: [], capabilities: { unchanged_checks: true }, action_result: receipt };
    expect(parseActivePlanResponse(value)).toEqual(value);
    expect(parsePlanCheckReceipt(receipt)).toEqual(receipt);
    expect(() => parseActivePlanResponse({ ...value, action_result: { ...receipt, checked_revision: 8 } })).toThrow();
    expect(() => parseActivePlanResponse({ ...value, active: null })).toThrow();
  });
  it.each([
    { capabilities: { unchanged_checks: "true" } },
    { capabilities: null },
    { action_result: { ...receipt, check_id: "not-an-event" } },
    { action_result: { ...receipt, recorded_at: "not-a-date" } },
    { action_result: { ...receipt, checked_revision: 0 } },
  ])("fails closed on invalid capability or receipt %#", extra => expect(() => parseActivePlanResponse({ active, history: [], ...extra })).toThrow());
});
