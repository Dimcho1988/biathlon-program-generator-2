import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { DELETE, GET, POST } from "../app/api/athlete/assistant/route";
import { currentAuthorizedAthlete } from "../lib/account-access";
import { waitForApi } from "../lib/api-readiness";

vi.mock("../lib/account-access", () => ({ currentAuthorizedAthlete: vi.fn() }));
vi.mock("../lib/api-readiness", () => ({ waitForApi: vi.fn() }));
const operation = "01111111-2222-4333-8444-555555555555";
const conversation = "02222222-2222-4333-8444-555555555555";
const access = { userId: "athlete-owner", actorUserId: "coach-user", athleteAlias: "athlete-a", displayName: "Спортист А", isOwner: false, canViewPlan: true, canViewRecovery: false, canEditPlan: false };
const input = { operation_id: operation, conversation_id: null, message: "Какво тренирам днес?" };
const request = (method = "POST", body: unknown = input, overrides: Record<string, string> = {}, query = "") => new Request(`https://web.example.test/api/athlete/assistant${query}`, {
  method, headers: { origin: "https://web.example.test", "Content-Type": "application/json", "X-Onflows-Athlete": access.athleteAlias, "X-Onflows-Actor": access.actorUserId, ...overrides },
  ...(method === "GET" ? {} : { body: JSON.stringify(body) }),
});
beforeEach(() => {
  vi.mocked(currentAuthorizedAthlete).mockResolvedValue(access);
  vi.stubEnv("ONFLOWS_API_BASE_URL", "https://api.example.test"); vi.stubEnv("ONFLOWS_SERVICE_TOKEN", "server-secret");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ message: "Запазената програма", conversation_id: conversation })));
});
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); vi.resetAllMocks(); });

it("allows a plan-only viewer and derives downstream athlete and actor exclusively from the authenticated access", async () => {
  const result = await POST(request());
  expect(result.status).toBe(200); expect(result.headers.get("Cache-Control")).toBe("no-store");
  expect(fetch).toHaveBeenCalledOnce();
  const [url, init] = vi.mocked(fetch).mock.calls[0];
  expect(String(url)).toBe("https://api.example.test/api/v2/athlete/assistant");
  const headers = new Headers(init?.headers);
  expect(headers.get("Authorization")).toBe("Bearer server-secret");
  expect(headers.get("X-OnFlows-Athlete-Alias")).toBe("athlete-a");
  expect(headers.get("X-OnFlows-Actor-Id")).toBe("coach-user");
  expect(JSON.parse(init!.body as string)).toEqual(input);
  expect(await result.text()).not.toContain("server-secret");
});

it("allows canonical plan actions to finish while keeping status reads bounded", async () => {
  const timeout = vi.spyOn(AbortSignal, "timeout");
  try {
    await POST(request());
    expect(timeout).toHaveBeenLastCalledWith(360_000);
    const postSignal = vi.mocked(fetch).mock.calls[0][1]?.signal;
    expect(postSignal).toBe(timeout.mock.results[0].value);
    await GET(request("GET", null, {}, `?operation_id=${operation}`));
    expect(timeout).toHaveBeenLastCalledWith(20_000);
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(JSON.parse(vi.mocked(fetch).mock.calls[0][1]!.body as string).operation_id).toBe(operation);
  } finally { timeout.mockRestore(); }
});

it.each(["https://hostile.test", "null", ""])("rejects a POST from another or absent origin: %s", async origin => {
  const result = await POST(request("POST", input, { origin }));
  expect(result.status).toBe(403); expect(currentAuthorizedAthlete).not.toHaveBeenCalled(); expect(fetch).not.toHaveBeenCalled();
});

it("requires a freshly authorized account on each request", async () => {
  await POST(request());
  vi.mocked(currentAuthorizedAthlete).mockResolvedValueOnce(null);
  expect((await POST(request())).status).toBe(401);
  expect(currentAuthorizedAthlete).toHaveBeenCalledTimes(2); expect(fetch).toHaveBeenCalledOnce();
});

it.each([{ "X-Onflows-Athlete": "athlete-b" }, { "X-Onflows-Actor": "other-coach" }, { "X-Onflows-Athlete": "", "X-Onflows-Actor": "" }] as Record<string, string>[])("rejects a forged or stale browser scope", async overrides => {
  expect((await POST(request("POST", input, overrides))).status).toBe(412); expect(fetch).not.toHaveBeenCalled();
});

it("denies a viewer with no current data permissions before contacting the API", async () => {
  vi.mocked(currentAuthorizedAthlete).mockResolvedValueOnce({ ...access, canViewPlan: false, canViewRecovery: false });
  expect((await POST(request())).status).toBe(403); expect(fetch).not.toHaveBeenCalled();
});

it.each([
  { ...input, athlete_alias: "athlete-b" }, { ...input, actor_id: "admin" }, { ...input, command: { action: "APPROVE" } },
  { ...input, operation_id: "invalid" }, { ...input, conversation_id: "invalid" }, { ...input, message: " " }, { ...input, message: "x".repeat(4001) },
])("rejects unrecognized fields and malformed turn inputs", async body => {
  expect((await POST(request("POST", body))).status).toBe(422); expect(fetch).not.toHaveBeenCalled();
});

it("bounds the actual UTF-8 body and the declared content length before proxying", async () => {
  expect((await POST(request("POST", { ...input, message: "а".repeat(10000) }))).status).toBe(413);
  expect((await POST(request("POST", input, { "content-length": "20001" }))).status).toBe(413);
  expect(fetch).not.toHaveBeenCalled(); expect(waitForApi).not.toHaveBeenCalled();
});

it("accepts only UUID conversation and operation query parameters", async () => {
  expect((await GET(request("GET", null, {}, `?conversation_id=${conversation}&operation_id=${operation}`))).status).toBe(200);
  const [url] = vi.mocked(fetch).mock.calls[0]; expect(String(url)).toContain(`conversation_id=${conversation}`); expect(String(url)).toContain(`operation_id=${operation}`);
  vi.mocked(fetch).mockClear();
  for (const query of ["?conversation_id=other", "?athlete_alias=athlete-b", "?operation_id=undefined"]) expect((await GET(request("GET", null, {}, query))).status).toBe(422);
  expect(fetch).not.toHaveBeenCalled();
});

it.each([409, 412])( "preserves a downstream revision or profile conflict (%s) without retries", async status => {
  vi.mocked(fetch).mockResolvedValueOnce(Response.json({ detail: "Променена версия или достъп." }, { status }));
  const result = await POST(request());
  expect(result.status).toBe(status); expect(await result.json()).toEqual({ error: "Променена версия или достъп." }); expect(fetch).toHaveBeenCalledOnce();
});

it("requires exact conversation identity and same-origin authorization for deletion", async () => {
  expect((await DELETE(request("DELETE", { conversation_id: conversation, delete_reports: true }))).status).toBe(422);
  expect((await DELETE(request("DELETE", { conversation_id: conversation }, { origin: "https://hostile.test" }))).status).toBe(403);
  expect(fetch).not.toHaveBeenCalled();
  expect((await DELETE(request("DELETE", { conversation_id: conversation }))).status).toBe(200);
  expect(vi.mocked(fetch).mock.calls[0][1]?.method).toBe("DELETE");
});
