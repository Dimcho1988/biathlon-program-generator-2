import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { GET, PUT } from "../app/api/athlete/management/[...path]/route";
import { currentAuthorizedAthlete } from "../lib/account-access";
import { revalidatePath } from "next/cache";

vi.mock("../lib/account-access", () => ({ currentAuthorizedAthlete: vi.fn() }));
vi.mock("next/cache", () => ({ revalidatePath: vi.fn() }));
vi.mock("../lib/api-readiness", () => ({ waitForApi: vi.fn() }));
const context = (path: string[]) => ({ params: Promise.resolve({ path }) });
const prefs = { enabled: true, overrides: { XC01: { speed_multiplier: [.98, 1.02] } } };
const request = () => new Request("https://web.test/api/athlete/management/methods/preferences", { method: "PUT", headers: { Origin: "https://web.test", "Content-Type": "application/json" }, body: JSON.stringify({ expected_revision: 7, preferences: prefs }) });
beforeEach(() => {
  process.env.ONFLOWS_API_BASE_URL = "https://api.test"; process.env.ONFLOWS_SERVICE_TOKEN = "private-server-token";
  vi.mocked(currentAuthorizedAthlete).mockResolvedValue({ athleteAlias: "selected-athlete", actorUserId: "actor", canViewPlan: true, canEditPlan: true } as never);
});
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); delete process.env.ONFLOWS_API_BASE_URL; delete process.env.ONFLOWS_SERVICE_TOKEN; });

it("forwards read phase and selected athlete identity, never arbitrary client identity", async () => {
  const fetchMock = vi.fn().mockResolvedValue(Response.json({ methods: [] })); vi.stubGlobal("fetch", fetchMock);
  const result = await GET(new Request("https://web.test/api/athlete/management/methods?phase=SPECIAL_PREPARATION&athlete_alias=other"), context(["methods"]));
  expect(result.status).toBe(200);
  const [url, init] = fetchMock.mock.calls[0];
  expect(String(url)).toBe("https://api.test/api/v2/athlete/management/methods?phase=SPECIAL_PREPARATION");
  expect(init.headers["X-OnFlows-Athlete-Alias"]).toBe("selected-athlete"); expect(init.headers["X-OnFlows-Actor-Id"]).toBe("actor");
  expect(result.headers.get("Cache-Control")).toBe("no-store");
});
it("requires plan access for reading and edit access plus same-origin for saving", async () => {
  const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock);
  vi.mocked(currentAuthorizedAthlete).mockResolvedValue(null);
  expect((await GET(new Request("https://web.test/api/athlete/management/methods"), context(["methods"]))).status).toBe(401);
  vi.mocked(currentAuthorizedAthlete).mockResolvedValue({ canViewPlan: false } as never);
  expect((await GET(new Request("https://web.test/api/athlete/management/methods"), context(["methods"]))).status).toBe(403);
  vi.mocked(currentAuthorizedAthlete).mockResolvedValue({ canViewPlan: true, canEditPlan: false } as never);
  expect((await PUT(request(), context(["methods", "preferences"]))).status).toBe(403);
  expect((await PUT(new Request("https://web.test/api/athlete/management/methods/preferences", { method: "PUT", headers: { Origin: "https://evil.test" } }), context(["methods", "preferences"]))).status).toBe(403);
  expect(fetchMock).not.toHaveBeenCalled();
});
it("sends only the versioned preferences and invalidates affected pages", async () => {
  const fetchMock = vi.fn().mockResolvedValue(Response.json({ revision: 8, preferences: prefs })); vi.stubGlobal("fetch", fetchMock);
  expect((await PUT(request(), context(["methods", "preferences"]))).status).toBe(200);
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ expected_revision: 7, preferences: prefs });
  expect(revalidatePath).toHaveBeenCalledWith("/methods"); expect(revalidatePath).toHaveBeenCalledWith("/management");
});
it("preserves revision conflicts and rejects unsupported endpoints and phase values", async () => {
  const fetchMock = vi.fn().mockResolvedValue(Response.json({ error: "revision changed" }, { status: 409 })); vi.stubGlobal("fetch", fetchMock);
  expect((await PUT(request(), context(["methods", "preferences"]))).status).toBe(409);
  expect((await GET(new Request("https://web.test/api/athlete/management/methods?phase=UNSAFE"), context(["methods"]))).status).toBe(422);
  expect((await GET(new Request("https://web.test/api/athlete/management/methods/preferences"), context(["methods", "preferences"]))).status).toBe(404);
  expect(fetchMock).toHaveBeenCalledTimes(1);
});
