import { beforeEach, expect, it, vi } from "vitest";
const access = vi.hoisted(() => vi.fn());
const origin = vi.hoisted(() => vi.fn());
vi.mock("../lib/account-access", () => ({ currentAuthorizedAthlete: access }));
vi.mock("../lib/account-route", () => ({ isSameOrigin: origin }));
vi.mock("../lib/api-readiness", () => ({ waitForApi: vi.fn() }));
import { POST } from "../app/api/athlete/energy/race-loading/route";
import { GET } from "../app/api/athlete/energy/race-reminders/route";
const request = () => new Request("https://example.org/api/athlete/energy/race-loading", { method: "POST", body: JSON.stringify({ event_id: "race", preview_hash: "a".repeat(64), action: "ACCEPTED", expected_revision: 0 }) });
beforeEach(() => {
  vi.stubEnv("ONFLOWS_API_BASE_URL", "https://api.example.org"); vi.stubEnv("ONFLOWS_SERVICE_TOKEN", "test-only");
  origin.mockReturnValue(true);
  access.mockResolvedValue({ athleteAlias: "authorized", actorUserId: "actor", canViewRecovery: true, canViewPlan: true, isOwner: true });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ saved: true, revision: 1, events: [] }), {status:200})));
});
it.each([{ canViewRecovery: true, canViewPlan: false }, { canViewRecovery: false, canViewPlan: true }])("requires both read scopes %j", async (rights) => {
  access.mockResolvedValue({ ...rights, isOwner: false, canEditPlan: true });
  expect((await GET()).status).toBe(403); expect((await POST(request())).status).toBe(403);
  expect(fetch).not.toHaveBeenCalled();
});
it("requires edit rights for decisions, but allows authorized read reminders", async () => {
  access.mockResolvedValue({ canViewRecovery: true, canViewPlan: true, isOwner:false, canEditPlan:false, athleteAlias:"viewer" });
  expect((await POST(request())).status).toBe(403); expect((await GET()).status).toBe(200);
});
it("rejects forged origin and derives scope from the authenticated session", async () => {
  origin.mockReturnValue(false); expect((await POST(request())).status).toBe(403); expect(fetch).not.toHaveBeenCalled();
  origin.mockReturnValue(true); expect((await POST(request())).status).toBe(200);
  const [url, options] = vi.mocked(fetch).mock.calls[0];
  expect(String(url)).toContain("/api/v2/athlete/energy/race-loading");
  expect(options?.headers).toMatchObject({"X-OnFlows-Athlete-Alias":"authorized", "X-OnFlows-Actor-Id":"actor"});
});
it("does not cache or fabricate reminders on an upstream error", async () => {
  vi.mocked(fetch).mockResolvedValue(new Response("", {status:503}));
  expect((await GET()).status).toBe(503);
});
