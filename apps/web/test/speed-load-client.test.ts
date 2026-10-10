// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { SpeedLoad } from "../lib/speed-load";
import type { SpeedLoadRequest } from "../lib/speed-load-client";

const storageKey = "onflows.speed-load-cache.v1";
const etag = `"${"a".repeat(64)}"`;
const fixture = (): SpeedLoad => ({
  schema_version: "speed-load-history-v1", model_version: "independent-speed-load-causal-ti-v1",
  status: "PARTIAL", sport: null, sports: ["Run"], source_generation_id: "g", source_revision: 1,
  start_date: "2026-09-01", end_date: "2026-10-01", load_role: "PARALLEL_ESTIMATE_NOT_ADDED_TO_HR",
  mapping_policy: "SAME_SPORT_PRIOR_40_DAYS_EXCLUDING_CURRENT_DAY", recorded_minutes: 40,
  classified_minutes: 20, coverage_percent: 50,
  zones: Array.from({ length: 5 }, (_, i) => ({ zone: `Z${i + 1}`, minutes: 4, equivalent_minutes: 3,
    effective_load: 7, e7_daily: 1, e40_daily: 2, ratio_7_40: .8 })),
  daily: [], sport_indices: [], activities: [], warnings: [],
});
const request: SpeedLoadRequest = { cacheScope: "account-athlete-A", generation: "g", revision: 1 };
const ok = (data = fixture(), tag = etag) => new Response(JSON.stringify(data), { headers: { ETag: tag } });
let read: typeof import("../lib/speed-load-client").readSpeedLoad;
let clear: typeof import("../lib/speed-load-client").clearSpeedLoadClientCache;
beforeEach(async () => {
  vi.resetModules(); sessionStorage.clear();
  ({ readSpeedLoad: read, clearSpeedLoadClientCache: clear } = await import("../lib/speed-load-client"));
});
afterEach(() => { clear(); vi.unstubAllGlobals(); vi.useRealTimers(); });

it("revalidates across navigation and reload, reusing only an authorized 304 without parsing JSON", async () => {
  const parse = vi.fn();
  const fetcher = vi.fn().mockResolvedValueOnce(ok()).mockResolvedValue({ status: 304, json: parse });
  vi.stubGlobal("fetch", fetcher);
  const first = await read(request);
  expect(await read(request)).toBe(first);
  expect(fetcher.mock.calls[1][1].headers).toEqual({ "If-None-Match": etag });
  expect(parse).not.toHaveBeenCalled();
  vi.resetModules();
  const afterReload = await import("../lib/speed-load-client");
  expect(await afterReload.readSpeedLoad(request)).toEqual(first);
  expect(fetcher.mock.calls[2][1].headers).toEqual({ "If-None-Match": etag });
  afterReload.clearSpeedLoadClientCache();
});

it("deduplicates ten concurrent identical requests and preserves exact results", async () => {
  let resolve!: (value: Response) => void;
  const fetcher = vi.fn().mockImplementation(() => new Promise(done => { resolve = done; }));
  vi.stubGlobal("fetch", fetcher);
  const calls = Array.from({ length: 10 }, () => read(request));
  expect(fetcher).toHaveBeenCalledTimes(1);
  resolve(ok());
  const result = await Promise.all(calls);
  expect(result).toHaveLength(10);
  for (const value of result) expect(value).toEqual(fixture());
});

it("rechecks changed settings and repaired results even under the same generation", async () => {
  const changed = { ...fixture(), zones: fixture().zones.map(z => ({ ...z, effective_load: 11 })) };
  const fetcher = vi.fn().mockResolvedValueOnce(ok()).mockResolvedValueOnce(ok(changed, `"${"b".repeat(64)}"`))
    .mockResolvedValueOnce(ok({ ...changed, activities: [{ activity_ref: "fixed", date: "2026-09-20", sport: "Run", recorded_minutes: 40,
      classified_minutes: 20, reason: "SPEED_RECOMPUTATION_REQUIRED" }] }));
  vi.stubGlobal("fetch", fetcher);
  await read(request);
  expect((await read(request)).zones[0].effective_load).toBe(11);
  expect(fetcher.mock.calls[1][1].headers).toEqual({ "If-None-Match": etag });
  await read(request);
  expect(JSON.parse(sessionStorage.getItem(storageKey)!)).toHaveLength(0);
});

it("isolates accounts, athletes, dates, sports and new generation revisions", async () => {
  const fetcher = vi.fn().mockImplementation(async (url: string) => {
    const q = new URL(url, "https://onflows.test").searchParams;
    return ok({ ...fixture(), sport: q.get("sport"), start_date: q.get("period_start") ?? fixture().start_date,
      source_generation_id: q.get("cache_scope") === "new-generation" ? "g2" : "g",
      source_revision: q.get("cache_scope") === "new-generation" ? 2 : 1 });
  });
  vi.stubGlobal("fetch", fetcher);
  await read(request);
  await read({ ...request, cacheScope: "other-account" });
  await read({ ...request, cacheScope: "other-athlete" });
  await read({ ...request, start: "2026-09-02", end: "2026-10-01" });
  await read({ ...request, sport: "Run" });
  await read({ ...request, generation: "g2", revision: 2, cacheScope: "new-generation" });
  expect(fetcher).toHaveBeenCalledTimes(6);
  for (const [, options] of fetcher.mock.calls) expect(options.headers).toBeUndefined();
});

it("revalidates the implicit current period after local midnight without accepting yesterday's derived report", async () => {
  const nextDay={...fixture(),start_date:"2026-09-02",end_date:"2026-10-02"};
  const fetcher=vi.fn().mockResolvedValueOnce(ok()).mockResolvedValueOnce(ok(nextDay,`"${"b".repeat(64)}"`));
  vi.stubGlobal("fetch",fetcher);
  await read(request);
  expect(await read(request)).toEqual(nextDay);
  expect(fetcher.mock.calls[1][1].headers).toEqual({"If-None-Match":etag});
});

it("keeps a snapshot date through reload and refresh without sharing the implicit current-day cache", async () => {
  const nextDay = { ...fixture(), start_date: "2026-09-02", end_date: "2026-10-02" };
  const fetcher = vi.fn().mockResolvedValueOnce(ok())
    .mockResolvedValueOnce(ok(nextDay, `"${"b".repeat(64)}"`))
    .mockResolvedValueOnce({ status: 304 }).mockResolvedValueOnce(ok());
  vi.stubGlobal("fetch", fetcher);
  const snapshot = { ...request, asOf: "2026-10-01" };
  await read(snapshot);
  expect(new URL(fetcher.mock.calls[0][0], "https://onflows.test").searchParams.get("as_of")).toBe("2026-10-01");
  expect((await read(request)).end_date).toBe("2026-10-02");
  expect(fetcher.mock.calls[1][1].headers).toBeUndefined();
  vi.resetModules();
  const reloaded = await import("../lib/speed-load-client");
  expect((await reloaded.readSpeedLoad(snapshot)).end_date).toBe("2026-10-01");
  expect(fetcher.mock.calls[2][1].headers).toEqual({ "If-None-Match": etag });
  await reloaded.readSpeedLoad({ ...snapshot, force: true });
  expect(fetcher.mock.calls[3][0]).toBe(fetcher.mock.calls[0][0]);
  expect(fetcher.mock.calls[3][1].headers).toBeUndefined();
  reloaded.clearSpeedLoadClientCache();
});

it("isolates two snapshot dates within the same generation and rejects an ignored analysis date", async () => {
  const nextDay = { ...fixture(), start_date: "2026-09-02", end_date: "2026-10-02" };
  const fetcher = vi.fn().mockResolvedValueOnce(ok()).mockResolvedValueOnce(ok(nextDay))
    .mockResolvedValueOnce(ok(nextDay));
  vi.stubGlobal("fetch", fetcher);
  await read({ ...request, asOf: "2026-10-01" });
  await read({ ...request, asOf: "2026-10-02" });
  expect(fetcher.mock.calls[1][1].headers).toBeUndefined();
  await expect(read({ ...request, asOf: "2026-10-01" })).rejects.toThrow("датата на пулсовия анализ");
});

it("never serves a previous result after revoked access or a selected profile race", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(ok()).mockResolvedValueOnce(new Response(null, { status: 403 }))
    .mockResolvedValueOnce(new Response(null, { status: 409 }));
  vi.stubGlobal("fetch", fetcher);
  await read(request);
  await expect(read(request)).rejects.toThrow("временно не е достъпен");
  expect(JSON.parse(sessionStorage.getItem(storageKey)!)).toHaveLength(0);
  await expect(read(request)).rejects.toThrow("профил е променен");
  expect(fetcher.mock.calls[2][1].headers).toBeUndefined();
});

it("allows one consumer to abort without cancelling another, and aborts after the last consumer leaves", async () => {
  let resolve!: (value: Response) => void;
  const fetcher = vi.fn().mockImplementation(() => new Promise(done => { resolve = done; }));
  vi.stubGlobal("fetch", fetcher);
  const first = new AbortController(), second = new AbortController();
  const cancelled = read({ ...request, signal: first.signal });
  const remaining = read({ ...request, signal: second.signal });
  first.abort();
  await expect(cancelled).rejects.toThrow("Aborted");
  expect(fetcher.mock.calls[0][1].signal.aborted).toBe(false);
  resolve(ok()); await expect(remaining).resolves.toEqual(fixture());
  const last = new AbortController();
  const outstanding = read({ ...request, cacheScope: "new", signal: last.signal });
  last.abort(); await expect(outstanding).rejects.toThrow("Aborted");
  expect(fetcher.mock.calls[1][1].signal.aborted).toBe(true);
  resolve(ok());
});

it("does not let a delayed old cancellation remove the replacement request's cached result", async () => {
  let rejectOld!: (error: Error) => void;
  const fetcher=vi.fn().mockImplementationOnce(()=>new Promise((_resolve,reject)=>{rejectOld=reject;}))
    .mockResolvedValueOnce(ok()).mockResolvedValueOnce({status:304});
  vi.stubGlobal("fetch",fetcher);
  const controller=new AbortController();
  const old=read({...request,signal:controller.signal});
  controller.abort();await expect(old).rejects.toThrow("Aborted");
  await read(request);
  rejectOld(new Error("Delayed abort"));
  await Promise.resolve();await Promise.resolve();
  expect(await read(request)).toEqual(fixture());
  expect(fetcher.mock.calls[2][1].headers).toEqual({"If-None-Match":etag});
});

it("bounds stored results, expires validators, and purges data on signout", async () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date("2026-10-03T00:00:00Z"));
  const fetcher = vi.fn().mockImplementation(async () => ok()); vi.stubGlobal("fetch", fetcher);
  for (let i = 0; i < 12; i++) await read({ ...request, cacheScope: String(i) });
  expect(JSON.parse(sessionStorage.getItem(storageKey)!)).toHaveLength(8);
  expect(sessionStorage.getItem(storageKey)!.length * 2).toBeLessThanOrEqual(2 * 1024 * 1024);
  vi.advanceTimersByTime(5 * 60 * 1000 + 1);
  await read({ ...request, cacheScope: "11" });
  expect(fetcher.mock.calls.at(-1)![1].headers).toBeUndefined();
  clear(); expect(sessionStorage.getItem(storageKey)).toBeNull();
  await read({ ...request, cacheScope: "11" });
  expect(fetcher.mock.calls.at(-1)![1].headers).toBeUndefined();
});

it("bypasses validators for explicit refresh and works with storage disabled", async () => {
  const fetcher = vi.fn().mockImplementation(async () => ok()); vi.stubGlobal("fetch", fetcher);
  const denied = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("disabled"); });
  await read(request); await read({ ...request, force: true });
  expect(fetcher.mock.calls[1][1].headers).toBeUndefined();
  denied.mockRestore();
});
