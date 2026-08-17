const WAKE_WINDOW_MS = 75_000;
const PROBE_TIMEOUT_MS = 10_000;
const RETRY_DELAY_MS = 3_000;

const retryableInfrastructureStatus = (status: number) => status === 502 || status === 503 || status === 504;
const pause = (milliseconds: number) => new Promise((resolve) => setTimeout(resolve, milliseconds));
const pendingChecks = new Map<string, Promise<void>>();

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
      throw new Error(`API health check failed (${response.status})`);
    const remaining = deadline - Date.now();
    if (remaining > 0) await pause(Math.min(RETRY_DELAY_MS, remaining));
  }
  throw new Error("API did not wake in time");
}

export function waitForApi(baseUrl: string): Promise<void> {
  const existing = pendingChecks.get(baseUrl);
  if (existing) return existing;
  const check = probeUntilReady(baseUrl).finally(() => pendingChecks.delete(baseUrl));
  pendingChecks.set(baseUrl, check);
  return check;
}

export { retryableInfrastructureStatus };
