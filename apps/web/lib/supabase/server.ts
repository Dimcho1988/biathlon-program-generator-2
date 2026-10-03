import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";
import { supabasePublicConfig } from "./config";

type ServerClientOptions = {
  requestTimeoutMs?: number;
};

export async function createClient(options: ServerClientOptions = {}) {
  const cookieStore = await cookies();
  const { url, publishableKey } = supabasePublicConfig();
  const timedFetch = options.requestTimeoutMs
    ? (input: RequestInfo | URL, init?: RequestInit) => fetch(input, {
        ...init,
        signal: init?.signal
          ? AbortSignal.any([init.signal, AbortSignal.timeout(options.requestTimeoutMs!)])
          : AbortSignal.timeout(options.requestTimeoutMs!),
      })
    : undefined;

  return createServerClient(url, publishableKey, {
    ...(timedFetch ? { global: { fetch: timedFetch } } : {}),
    cookies: {
      getAll: () => cookieStore.getAll(),
      setAll(cookiesToSet) {
        try {
          cookiesToSet.forEach(({ name, value, options }) =>
            cookieStore.set(name, value, options));
        } catch {
          // Server Components are read-only; proxy.ts refreshes their cookies.
        }
      },
    },
  });
}
