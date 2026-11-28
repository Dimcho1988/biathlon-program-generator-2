import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { GET } from "../app/api/athlete/management/[...path]/route";
import { currentAuthorizedAthlete } from "../lib/account-access";
vi.mock("../lib/account-access", () => ({currentAuthorizedAthlete: vi.fn()}));
vi.mock("../lib/api-readiness", () => ({waitForApi: vi.fn()}));
vi.mock("next/cache", () => ({revalidatePath: vi.fn()}));
const context = {params: Promise.resolve({path: ["speed-comparison"]})};
const access = {userId: "athlete", actorUserId: "coach", athleteAlias: "selected-athlete", displayName: "A", isOwner: false, canEditPlan: false, canViewPlan: true, canViewRecovery: true};
const request = (query: string) => new Request(`https://app.test/api/athlete/management/speed-comparison?${query}`, {headers: {"X-Onflows-Athlete": "selected-athlete", "X-Onflows-Actor": "coach"}});
beforeEach(() => {vi.stubEnv("ONFLOWS_API_BASE_URL", "https://api.test"); vi.stubEnv("ONFLOWS_SERVICE_TOKEN", "test-token"); vi.mocked(currentAuthorizedAthlete).mockResolvedValue(access);});
afterEach(() => {vi.unstubAllGlobals();vi.unstubAllEnvs();vi.resetAllMocks();});

it("requires both shared plan and recovery access before fetching actual speed data", async () => {
  const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
  vi.mocked(currentAuthorizedAthlete).mockResolvedValue({...access, canViewRecovery: false});
  expect((await GET(request("kind=ACTIVE&revision=4"), context)).status).toBe(403);
  vi.mocked(currentAuthorizedAthlete).mockResolvedValue({...access, canViewPlan: false});
  expect((await GET(request("kind=ACTIVE&revision=4"), context)).status).toBe(403);
  expect(fetcher).not.toHaveBeenCalled();
});

it("forwards only a validated stored-plan identity with the authorized athlete scope", async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json({sports: []})); vi.stubGlobal("fetch", fetcher);
  expect((await GET(request("kind=DRAFT&revision=4&start_date=2026-10-20"), context)).status).toBe(200);
  const [url, options] = fetcher.mock.calls[0];
  expect(String(url)).toBe("https://api.test/api/v2/athlete/management/speed-comparison?kind=DRAFT&revision=4&start_date=2026-10-20");
  expect(options.headers["X-OnFlows-Athlete-Alias"]).toBe("selected-athlete");
  expect(options.method).toBe("GET"); expect(options.body).toBeUndefined();
  for (const query of ["kind=DRAFT&revision=4", "kind=ACTIVE&revision=0", "kind=ACTIVE&revision=4&start_date=2026-10-20", "kind=ACTIVE&revision=4&athlete_alias=other"])
    expect((await GET(request(query), context)).status).toBe(422);
  expect(fetcher).toHaveBeenCalledTimes(1);
});
