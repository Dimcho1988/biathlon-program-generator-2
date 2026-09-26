// Render Free documents an approximately one-minute cold start, but deploys and
// busy shared capacity can take longer. Keep a generous preview-only window;
// an already-running API returns on the first probe, so paid instances are not
// delayed by this safeguard.
const WAKE_WINDOW_MS = 150_000;
const PROBE_TIMEOUT_MS = 10_000;
const RETRY_DELAY_MS = 3_000;

const retryableInfrastructureStatus = (status: number) => status === 502 || status === 503 || status === 504;
const pause = (milliseconds: number) => new Promise((resolve) => setTimeout(resolve, milliseconds));
const pendingChecks = new Map<string, Promise<void>>();
const readyUntil = new Map<string, number>();
const READY_TTL_MS = 30_000;

export function markApiUnavailable(baseUrl: string) {
  readyUntil.delete(baseUrl);
}

export class ApiReadinessError extends Error {
  constructor(public readonly status: number) { super(`API health check failed (${status})`); }
}

async function probeUntilReady(baseUrl: string) {
  const deadline = Date.now() + WAKE_WINDOW_MS;
  while (Date.now() < deadline) {
    let response: Response | null = null;
    try {
      const remaining = Math.max(1, deadline - Date.now());
      response = await fetch(new URL("/health", baseUrl), {
        cache: "no-store",
        signal: AbortSignal.timeout(Math.min(PROBE_TIMEOUT_MS, remaining)),
      });
    } catch {
      // A waking Render Free proxy can briefly reset or time out the connection.
    }
    if (response?.ok) return;
    if (response && !retryableInfrastructureStatus(response.status))
      throw new ApiReadinessError(response.status);
    const remaining = deadline - Date.now();
    if (remaining > 0) await pause(Math.min(RETRY_DELAY_MS, remaining));
  }
  throw new Error("API did not wake in time");
}

export function waitForApi(baseUrl: string): Promise<void> {
  // Health only: never cache athlete data or authorization across requests.
  if ((readyUntil.get(baseUrl) ?? 0) > Date.now()) return Promise.resolve();
  const existing = pendingChecks.get(baseUrl);
  if (existing) return existing;
  const check = probeUntilReady(baseUrl)
    .then(() => { readyUntil.set(baseUrl, Date.now() + READY_TTL_MS); })
    .catch((error) => { markApiUnavailable(baseUrl); throw error; })
    .finally(() => pendingChecks.delete(baseUrl));
  pendingChecks.set(baseUrl, check);
  return check;
}

export { retryableInfrastructureStatus };
