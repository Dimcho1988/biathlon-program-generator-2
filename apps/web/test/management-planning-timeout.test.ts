import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { POST } from "../app/api/athlete/management/[...path]/route";
import { currentAuthorizedAthlete } from "../lib/account-access";

vi.mock("../lib/account-access", () => ({ currentAuthorizedAthlete: vi.fn() }));
vi.mock("next/cache", () => ({ revalidatePath: vi.fn() }));
vi.mock("../lib/api-readiness", () => ({ waitForApi: vi.fn() }));

function send(path: string, body: Record<string, unknown>) {
  return POST(new Request(`https://web.test/api/athlete/management/${path}`, {
    method: "POST", headers: { Origin: "https://web.test", "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }), { params: Promise.resolve({ path: [path] }) });
}

beforeEach(() => {
  vi.useFakeTimers();
  process.env.ONFLOWS_API_BASE_URL = "https://api.test";
  process.env.ONFLOWS_SERVICE_TOKEN = "test-service-token";
  vi.mocked(currentAuthorizedAthlete).mockResolvedValue({ athleteAlias: "test-athlete", actorUserId: "test-actor", canViewPlan: true, canEditPlan: true } as never);
  // Use controllable timers while preserving actual AbortSignal semantics.
  vi.spyOn(AbortSignal, "timeout").mockImplementation(ms => {
    const controller = new AbortController();
    setTimeout(() => controller.abort(new DOMException("Deadline", "TimeoutError")), ms);
    return controller.signal;
  });
});
afterEach(() => {
  vi.clearAllTimers(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals();
  delete process.env.ONFLOWS_API_BASE_URL; delete process.env.ONFLOWS_SERVICE_TOKEN;
});

function delayedApi(milliseconds: number) {
  vi.stubGlobal("fetch", vi.fn((_url: URL, init: RequestInit) => new Promise<Response>((resolve, reject) => {
    const timer = setTimeout(() => resolve(Response.json({ saved: true, revision: 2 })), milliseconds);
    init.signal?.addEventListener("abort", () => { clearTimeout(timer); reject(init.signal?.reason); }, { once: true });
  })));
}

it.each([
  ["generate", { start_date: "2026-10-12", expected_profile_revision: 1, expected_draft_revision: 0 }],
  ["action", { action: "REFRESH", expected_revision: 1 }],
  ["action", { action: "RESUME", expected_revision: 1 }],
  ["day", { action: "REST", date: "2026-10-13", note: "", expected_revision: 1 }],
] as const)("returns a successful 82-second %s plan calculation without repeating the write", async (path, body) => {
  delayedApi(82_000); // Observed live planner latency exceeded the old 75-second deadline.
  const pending = send(path, body);
  await vi.advanceTimersByTimeAsync(82_000);
  const result = await pending;
  expect(result.status).toBe(200);
  expect(await result.json()).toEqual({ saved: true, revision: 2 });
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("keeps a bounded deadline for a stalled calculation without retrying a mutation", async () => {
  delayedApi(181_000);
  let settled = false;
  const pending = send("action", { action: "REFRESH", expected_revision: 1 }).then(r => { settled = true; return r; });
  await vi.advanceTimersByTimeAsync(179_999);
  expect(settled).toBe(false);
  await vi.advanceTimersByTimeAsync(1);
  expect((await pending).status).toBe(503);
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("retains the ordinary deadline for an action that does not calculate a plan", async () => {
  delayedApi(82_000);
  const pending = send("action", { action: "PAUSE", expected_revision: 1 });
  await vi.advanceTimersByTimeAsync(75_000);
  expect((await pending).status).toBe(503);
  expect(fetch).toHaveBeenCalledTimes(1);
});
