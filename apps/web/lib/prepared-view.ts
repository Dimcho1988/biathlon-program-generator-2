export const PREPARED_VIEW_MESSAGE = "Данните се подготвят във фонов режим. Показването се обновява автоматично.";

export class PreparedViewPending extends Error {
  constructor() { super(PREPARED_VIEW_MESSAGE); this.name = "PreparedViewPending"; }
}

/** Retry reads of a newly requested variant; writes are never replayed. */
export async function fetchPrepared(input: RequestInfo | URL, init: RequestInit = {}, request: typeof fetch = fetch): Promise<Response> {
  if (init.method && init.method !== "GET") return request(input, init);
  const deadline = Date.now() + 120000;
  let delay = 1500;
  for (;;) {
    init.signal?.throwIfAborted();
    const response = await request(input, init);
    if (response.status !== 503 || response.headers.get("X-OnFlows-Preparation") !== "pending") return response;
    if (Date.now() >= deadline) return response;
    await response.body?.cancel();
    await new Promise<void>((resolve, reject) => {
      const aborted = () => {clearTimeout(timer); reject(init.signal?.reason ?? new DOMException("Aborted", "AbortError"));};
      const timer = setTimeout(() => {init.signal?.removeEventListener("abort", aborted); resolve();}, delay);
      init.signal?.addEventListener("abort", aborted, {once:true});
      if (init.signal?.aborted) aborted();
    });
    delay = Math.min(8000, delay + 1000);
  }
}
