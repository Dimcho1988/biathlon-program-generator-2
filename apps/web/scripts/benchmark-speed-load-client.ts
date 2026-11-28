/** Synthetic client overhead only; excludes network, authorization and backend.
 * Run from apps/web: npx vite-node scripts/benchmark-speed-load-client.ts
 */
import { parseSpeedLoad, type SpeedLoad } from "../lib/speed-load";
import { clearSpeedLoadClientCache, readSpeedLoad } from "../lib/speed-load-client";

const stored = new Map<string, string>();
const storage: Storage = {
  get length() { return stored.size; }, clear: () => stored.clear(),
  getItem: key => stored.get(key) ?? null, key: index => [...stored.keys()][index] ?? null,
  removeItem: key => { stored.delete(key); }, setItem: (key, value) => { stored.set(key, value); },
};
Object.defineProperty(globalThis, "sessionStorage", { value: storage, configurable: true });
const tag = `"${"a".repeat(64)}"`;
const zoneRows = () => Array.from({ length: 5 }, (_, i) => ({ zone: `Z${i + 1}`, minutes: 40,
  equivalent_minutes: 32.345678, effective_load: 123.456789, e7_daily: 100, e40_daily: 80, ratio_7_40: 1.234567 }));
const report: SpeedLoad = {
  schema_version: "speed-load-history-v1", model_version: "independent-speed-load-causal-ti-v1", status: "PARTIAL",
  sport: null, sports: ["Run"], source_generation_id: "synthetic-generation", source_revision: 1,
  start_date: "2025-10-01", end_date: "2026-10-01", load_role: "PARALLEL_ESTIMATE_NOT_ADDED_TO_HR",
  mapping_policy: "SYNTHETIC_NOT_ATHLETE_DATA", recorded_minutes: 14640, classified_minutes: 7320, coverage_percent: 50,
  zones: zoneRows(), sport_indices: [], warnings: [],
  daily: Array.from({ length: 366 }, (_, day) => zoneRows().map(row => ({ date: new Date(Date.UTC(2025, 9, 1 + day)).toISOString().slice(0, 10),
    zone: row.zone, equivalent_minutes: row.equivalent_minutes, effective_load: row.effective_load, ratio_7_40: row.ratio_7_40 }))).flat(),
  activities: Array.from({ length: 366 }, (_, day) => ({ activity_ref: `synthetic-${day}`, date: new Date(Date.UTC(2025, 9, 1 + day)).toISOString().slice(0, 10),
    sport: "Run", recorded_minutes: 40, classified_minutes: 20, reason: null })),
};
const body = JSON.stringify(report);
const bytes = Buffer.byteLength(body);
const request = { cacheScope: "synthetic-scope", generation: report.source_generation_id, revision: report.source_revision };
let requests = 0, transferred = 0, parses = 0;
globalThis.fetch = (async (_input, options) => {
  requests += 1;
  if (new Headers(options?.headers).get("If-None-Match") === tag) return new Response(null, { status: 304 });
  transferred += bytes;
  const response = new Response(body, { headers: { ETag: tag } });
  const original = response.json.bind(response);
  response.json = async () => { parses += 1; return original(); };
  return response;
}) as typeof fetch;
const before = async () => parseSpeedLoad(await (await fetch("https://synthetic.test/speed-load")).json());
const after = () => readSpeedLoad(request);
const median = (values: number[]) => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)];
const measure = async (read: () => Promise<SpeedLoad>) => {
  const durations: number[] = []; requests = transferred = parses = 0;
  for (let i = 0; i < 80; i++) {
    const start = performance.now(); const value = await read(); durations.push(performance.now() - start);
    if (value.zones[0].effective_load !== report.zones[0].effective_load) throw new Error("Numeric mismatch");
  }
  return { median_ms: Number(median(durations).toFixed(3)), requests, response_bytes: transferred, json_parses: parses };
};
for (let i = 0; i < 20; i++) await before();
clearSpeedLoadClientCache(); await after();
const baseline = await measure(before), conditional = await measure(after);
if (JSON.stringify(await after()) !== body) throw new Error("Exact result mismatch");
requests = transferred = parses = 0; await Promise.all(Array.from({ length: 10 }, before)); const concurrentBefore = requests;
clearSpeedLoadClientCache(); requests = transferred = parses = 0; await Promise.all(Array.from({ length: 10 }, after)); const concurrentAfter = requests;
console.log(JSON.stringify({ scenario: "SYNTHETIC_366_DAYS_CLIENT_ONLY", report_bytes: bytes, repetitions: 80,
  baseline, conditional, concurrent_requests: { baseline: concurrentBefore, optimized: concurrentAfter }, exact_result_equal: true }, null, 2));
clearSpeedLoadClientCache();
