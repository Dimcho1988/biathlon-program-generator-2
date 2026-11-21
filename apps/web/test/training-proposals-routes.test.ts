import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { GET, POST, PUT } from "../app/api/athlete/management/[...path]/route";
import { currentAuthorizedAthlete } from "../lib/account-access";
import { revalidatePath } from "next/cache";

vi.mock("../lib/account-access", () => ({ currentAuthorizedAthlete: vi.fn() }));
vi.mock("next/cache", () => ({ revalidatePath: vi.fn() }));
vi.mock("../lib/api-readiness", () => ({ waitForApi: vi.fn() }));
const path = (suffix?: string) => ({ params: Promise.resolve({ path: ["training-proposals", ...(suffix ? [suffix] : [])] }) });
const proposal = { action: "REPEAT_STRUCTURE", source_start: "2026-11-02", source_end: "2026-11-15", target_start: "2026-11-16" };
const operation_id = "982a8946-01f2-4d72-a064-9ba28ab3f472";
function request(suffix: string, body: unknown, headers: Record<string, string> = {}) {
  return new Request(`https://web.test/api/athlete/management/training-proposals/${suffix}`, { method: "POST", headers: { Origin: "https://web.test", "Content-Type": "application/json", "X-Onflows-Athlete": "selected-athlete", "X-Onflows-Actor": "actor", ...headers }, body: JSON.stringify(body) });
}
beforeEach(() => {
  process.env.ONFLOWS_API_BASE_URL = "https://api.test"; process.env.ONFLOWS_SERVICE_TOKEN = "private-server-token";
  vi.mocked(currentAuthorizedAthlete).mockResolvedValue({ athleteAlias: "selected-athlete", actorUserId: "actor", canViewPlan: true, canEditPlan: true } as never);
});
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); delete process.env.ONFLOWS_API_BASE_URL; delete process.env.ONFLOWS_SERVICE_TOKEN; });
it("previews a two-week block without invalidating or changing the app state", async () => {
  const fetchMock = vi.fn().mockResolvedValue(Response.json({ preview: { proposal } })); vi.stubGlobal("fetch", fetchMock);
  expect((await POST(request("preview", { proposal }), path("preview"))).status).toBe(200);
  expect(String(fetchMock.mock.calls[0][0])).toBe("https://api.test/api/v2/athlete/management/training-proposals/preview");
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ proposal });
  expect(fetchMock.mock.calls[0][1].headers["X-OnFlows-Athlete-Alias"]).toBe("selected-athlete");
  expect(revalidatePath).not.toHaveBeenCalled();
});
it("applies a version-pinned proposal with the same idempotency key and invalidates affected pages", async () => {
  const fetchMock = vi.fn().mockResolvedValue(Response.json({ receipt: {} })); vi.stubGlobal("fetch", fetchMock);
  const body = { proposal: { ...proposal, source_revisions: [5, 7], expected_plan_revision: 7 }, expected_revision: 9, operation_id };
  expect((await POST(request("apply", body), path("apply"))).status).toBe(200);
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual(body);
  expect(revalidatePath).toHaveBeenCalledWith("/management"); expect(revalidatePath).toHaveBeenCalledWith("/methods");
});
it("rejects unauthorized, cross-athlete and non-versioned writes before forwarding", async () => {
  const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock);
  expect((await POST(request("preview", { proposal }, { "X-Onflows-Athlete": "other" }), path("preview"))).status).toBe(412);
  expect((await POST(request("preview", { proposal }, { Origin: "https://other.test" }), path("preview"))).status).toBe(403);
  expect((await POST(request("apply", { proposal }), path("apply"))).status).toBe(422);
  vi.mocked(currentAuthorizedAthlete).mockResolvedValue({ athleteAlias: "selected-athlete", actorUserId: "actor", canViewPlan: true, canEditPlan: false } as never);
  expect((await POST(request("preview", { proposal }), path("preview"))).status).toBe(403);
  expect(fetchMock).not.toHaveBeenCalled();
});
it("rejects copied doses and unsupported scientific coefficients in proposals", async () => {
  const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock);
  expect((await POST(request("preview", { proposal: { ...proposal, dose: 90 } }), path("preview"))).status).toBe(422);
  expect((await POST(request("preview", { proposal: { action: "PERSONAL_METHOD", method: { id: "PERSONAL-one", name: "Вариант", base_method_id: "RUN01", speed_multiplier: [2, 3] } } }), path("preview"))).status).toBe(422);
  expect((await POST(request("preview", { proposal: { ...proposal, source_end: "2026-11-12" } }), path("preview"))).status).toBe(422);
  expect(fetchMock).not.toHaveBeenCalled();
});
it("preserves a readable method correction failure and supports read-only state", async () => {
  const fetchMock = vi.fn().mockResolvedValueOnce(Response.json({ detail: "Липсва запазена програма за 10 ноември." }, { status: 422 })).mockResolvedValueOnce(Response.json({ revision: 9 })); vi.stubGlobal("fetch", fetchMock);
  const result = await POST(request("preview", { proposal }), path("preview"));
  expect(result.status).toBe(422); expect((await result.json()).error).toContain("10 ноември");
  vi.mocked(currentAuthorizedAthlete).mockResolvedValue({ athleteAlias: "selected-athlete", actorUserId: "actor", canViewPlan: true, canEditPlan: false } as never);
  expect((await GET(new Request("https://web.test/api/athlete/management/training-proposals", { headers: { "X-Onflows-Athlete": "selected-athlete", "X-Onflows-Actor": "actor" } }), path())).status).toBe(200);
});
it("preserves a populated athlete library when ordinary preferences exceed the small-command limit", async () => {
  const personal_methods = Array.from({ length: 20 }, (_, index) => ({ id: `PERSONAL-${index}`, name: `Вариант ${index}`, base_method_id: "RUN01", notes: "а".repeat(1000) }));
  const preferences = { enabled: true, overrides: {}, personal_methods };
  const fetchMock = vi.fn().mockResolvedValue(Response.json({ revision: 8, preferences })); vi.stubGlobal("fetch", fetchMock);
  const result = await PUT(new Request("https://web.test/api/athlete/management/methods/preferences", { method: "PUT", headers: { Origin: "https://web.test", "Content-Type": "application/json", "X-Onflows-Athlete": "selected-athlete", "X-Onflows-Actor": "actor" }, body: JSON.stringify({ expected_revision: 7, preferences }) }), { params: Promise.resolve({ path: ["methods", "preferences"] }) });
  expect(result.status).toBe(200); expect(JSON.parse(fetchMock.mock.calls[0][1].body).preferences.personal_methods).toEqual(personal_methods);
});
