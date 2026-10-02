import { parseSpeedLoad, type SpeedLoad } from "./speed-load";

const STORAGE_KEY = "onflows.speed-load-cache.v1";
const MAX_ENTRIES = 8;
const MAX_STORAGE_BYTES = 2 * 1024 * 1024;
const TTL_MS = 5 * 60 * 1000;
type Entry = { key: string; until: number; etag: string; data: SpeedLoad };
type Pending = { controller: AbortController; users: number; settled: boolean; promise: Promise<SpeedLoad> };
const entries = new Map<string, Entry>();
const pending = new Map<string, Pending>();
let restored = false;

export type SpeedLoadRequest = {
  cacheScope?: string;
  generation: string | null;
  revision: number | null;
  start?: string;
  end?: string;
  sport?: string;
  signal?: AbortSignal;
  errorMessage?: string;
  generationError?: string;
  force?: boolean;
};

function restore() {
  if (restored) return;
  restored = true;
  try {
    const saved: unknown = JSON.parse(sessionStorage.getItem(STORAGE_KEY) ?? "[]");
    if (!Array.isArray(saved)) return;
    for (const entry of saved.slice(-MAX_ENTRIES)) {
      if (typeof entry?.key !== "string" || typeof entry.etag !== "string" ||
          !/^"[a-f0-9]{64}"$/.test(entry.etag) || !Number.isFinite(entry.until) ||
          entry.until <= Date.now() || entry.until > Date.now() + TTL_MS) continue;
      try { entries.set(entry.key, { ...entry, data: parseSpeedLoad(entry.data) }); } catch { /* Drop obsolete/corrupt contracts. */ }
    }
  } catch { /* Private browsing and storage quotas must not break the report. */ }
}

function persist() {
  for (const [key, entry] of entries) if (entry.until <= Date.now()) entries.delete(key);
  while (entries.size > MAX_ENTRIES) entries.delete(entries.keys().next().value!);
  try {
    let saved = JSON.stringify([...entries.values()]);
    // JS strings use at most two bytes per code unit in browser storage.
    while (saved.length * 2 > MAX_STORAGE_BYTES && entries.size) {
      entries.delete(entries.keys().next().value!);
      saved = JSON.stringify([...entries.values()]);
    }
    sessionStorage.setItem(STORAGE_KEY, saved);
  } catch { /* Memory reuse remains available when session storage is disabled. */ }
}

export function clearSpeedLoadClientCache() {
  entries.clear();
  for (const request of pending.values()) request.controller.abort();
  pending.clear();
  restored = false;
  try { sessionStorage.removeItem(STORAGE_KEY); } catch { /* Storage may be disabled. */ }
}

function validate(result: SpeedLoad, request: SpeedLoadRequest) {
  if (result.source_generation_id !== request.generation || result.source_revision !== request.revision)
    throw new Error(request.generationError ?? "Данните са обновени. Презаредете страницата за съгласуван отчет.");
  if (request.start && (result.start_date !== request.start || result.end_date !== request.end))
    throw new Error("Скоростният отчет не съответства на избрания период. Опитайте след обновяването на услугата.");
  if (result.sport !== (request.sport || null))
    throw new Error("Скоростният отчет не съответства на избрания спорт.");
  return result;
}

async function fetchReport(request: SpeedLoadRequest, signal: AbortSignal, cached?: Entry) {
  const query = new URLSearchParams();
  if (request.sport) query.set("sport", request.sport);
  if (request.start) query.set("period_start", request.start);
  if (request.end) query.set("period_end", request.end);
  if (request.cacheScope) query.set("cache_scope", request.cacheScope);
  const response = await fetch(`/api/athlete/models/speed-load${query.size ? `?${query}` : ""}`, {
    signal, cache: "no-store", ...(cached ? { headers: { "If-None-Match": cached.etag } } : {}),
  });
  if (response.status === 304 && cached) return { data: validate(cached.data, request), etag: cached.etag, reused: true };
  if (response.status === 409) throw new Error("Избраният профил е променен. Презаредете страницата.");
  if (!response.ok) throw new Error(request.errorMessage ?? "Отчетът по скорост временно не е достъпен. Опитайте отново.");
  return { data: validate(parseSpeedLoad(await response.json()), request), etag: response.headers?.get("ETag") ?? "", reused: false };
}

function consume(request: Pending, signal?: AbortSignal): Promise<SpeedLoad> {
  request.users += 1;
  return new Promise((resolve, reject) => {
    let released = false;
    const release = () => {
      if (released) return;
      released = true;
      signal?.removeEventListener("abort", aborted);
      request.users -= 1;
      if (!request.users && !request.settled) request.controller.abort();
    };
    const aborted = () => { release(); reject(new DOMException("Aborted", "AbortError")); };
    if (signal?.aborted) { aborted(); return; }
    signal?.addEventListener("abort", aborted, { once: true });
    request.promise.then(value => { if (!released) { release(); resolve(value); } },
      error => { if (!released) { release(); reject(error); } });
  });
}

// Cross-navigation/reload reuse is always conditionally revalidated. Generation
// IDs alone are insufficient: settings and repaired shadow analyses can change
// the report without activating a new generation. A 304 is issued only after
// fresh route authorization and comparison of the complete derived result.
export function readSpeedLoad(request: SpeedLoadRequest): Promise<SpeedLoad> {
  const cacheable = Boolean(request.cacheScope && request.generation && request.revision !== null);
  if (!cacheable) {
    return fetchReport(request, request.signal ?? new AbortController().signal).then(result => result.data);
  }
  restore();
  const key = JSON.stringify([request.cacheScope, request.generation, request.revision, request.start ?? null, request.end ?? null, request.sport || null]);
  const shared = pending.get(key);
  if (shared && !shared.controller.signal.aborted) return consume(shared, request.signal);
  const entry = entries.get(key);
  const cached = !request.force && entry && entry.until > Date.now() ? entry : undefined;
  const current: Pending = { controller: new AbortController(), users: 0, settled: false, promise: Promise.resolve(null as unknown as SpeedLoad) };
  current.promise = fetchReport(request, current.controller.signal, cached).then(result => {
    if (current.controller.signal.aborted || pending.get(key) !== current) return result.data;
    // A 304 needs neither parsing nor a synchronous session-storage rewrite.
    // Keep the original expiry instead of retaining private data indefinitely.
    if (result.reused) return result.data;
    if (/^"[a-f0-9]{64}"$/.test(result.etag) &&
        !result.data.warnings.includes("SPEED_RECOMPUTATION_REQUIRED") &&
        !result.data.activities.some(activity => activity.reason === "SPEED_RECOMPUTATION_REQUIRED")) {
      entries.delete(key);
      entries.set(key, { key, until: Date.now() + TTL_MS, data: result.data, etag: result.etag });
      persist();
    } else { entries.delete(key); persist(); }
    return result.data;
  }).catch(error => {
    // A denied/revoked selection must not retain its previous private result.
    if (!current.controller.signal.aborted && pending.get(key) === current) { entries.delete(key); persist(); }
    throw error;
  }).finally(() => {
    current.settled = true;
    if (pending.get(key) === current) pending.delete(key);
  });
  pending.set(key, current);
  return consume(current, request.signal);
}
