import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createServerClient } from "@supabase/ssr";
import { NextRequest } from "next/server";
import { updateSession } from "../lib/supabase/proxy";

vi.mock("@supabase/ssr", () => ({ createServerClient: vi.fn() }));

type SessionCookie = {
  name: string;
  value: string;
  options: { path: string; sameSite: "lax"; httpOnly: boolean; secure: boolean; maxAge: number };
};
type CookieBridge = {
  getAll: () => { name: string; value: string }[];
  setAll: (cookies: SessionCookie[], headers: Record<string, string>) => void;
};

beforeEach(() => {
  vi.resetAllMocks();
  vi.stubEnv("NEXT_PUBLIC_SUPABASE_URL", "https://session-test.supabase.co");
  vi.stubEnv("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", "sb_publishable_session_test");
});
afterEach(() => { vi.unstubAllEnvs(); });

// This tests the application's cookie bridge with an SDK callback mock. It
// does not validate a real JWT or contact Supabase's refresh-token endpoint.
it("passes refreshed cookies to server rendering and persists the SDK cookies and cache protection in the returned response", async () => {
  const request = new NextRequest("https://onflows.test/", {
    headers: { Cookie: "sb-session-auth-token.0=old-token; sb-session-auth-token.2=old-extra-chunk; preference=dark" },
  });
  const options = { path: "/", sameSite: "lax" as const, httpOnly: false, secure: true, maxAge: 400 * 24 * 60 * 60 };
  const refreshed: SessionCookie[] = [
    { name: "sb-session-auth-token.0", value: "refreshed-first-chunk", options },
    { name: "sb-session-auth-token.1", value: "refreshed-second-chunk", options },
    { name: "sb-session-auth-token.2", value: "", options: { ...options, maxAge: 0 } },
  ];
  let bridge!: CookieBridge;
  const getClaims = vi.fn(async () => {
    expect(bridge.getAll()).toContainEqual({ name: "sb-session-auth-token.0", value: "old-token" });
    bridge.setAll(refreshed, { "Cache-Control": "private, no-store", Expires: "0", Pragma: "no-cache" });
    return { data: { claims: { sub: "verified-test-user" } }, error: null };
  });
  vi.mocked(createServerClient).mockImplementation((_url, _key, supplied) => {
    bridge = supplied!.cookies as CookieBridge;
    return { auth: { getClaims } } as never;
  });

  const response = await updateSession(request);

  expect(createServerClient).toHaveBeenCalledWith("https://session-test.supabase.co", "sb_publishable_session_test", expect.any(Object));
  expect(getClaims).toHaveBeenCalledOnce();
  for (const cookie of refreshed) {
    expect(request.cookies.get(cookie.name)?.value).toBe(cookie.value);
    expect(response.cookies.get(cookie.name)).toMatchObject({ name: cookie.name, value: cookie.value, ...cookie.options });
  }
  // The next server-rendering stage receives the newly refreshed cookie header,
  // rather than attempting another refresh using the previous refresh token.
  expect(response.headers.get("x-middleware-request-cookie")).toContain("sb-session-auth-token.0=refreshed-first-chunk");
  expect(response.headers.get("x-middleware-request-cookie")).toContain("sb-session-auth-token.1=refreshed-second-chunk");
  expect(request.cookies.get("preference")?.value).toBe("dark");
  expect(response.cookies.get("preference")).toBeUndefined();
  expect(response.headers.get("Cache-Control")).toBe("private, no-store");
  expect(response.headers.get("Expires")).toBe("0");
  expect(response.headers.get("Pragma")).toBe("no-cache");
});

it("verifies an existing session without rewriting its persistent cookies when the SDK does not refresh it", async () => {
  const request = new NextRequest("https://onflows.test/account", {
    headers: { Cookie: "sb-session-auth-token=current-token" },
  });
  const getClaims = vi.fn().mockResolvedValue({ data: { claims: { sub: "verified-test-user" } }, error: null });
  vi.mocked(createServerClient).mockReturnValue({ auth: { getClaims } } as never);

  const response = await updateSession(request);

  expect(getClaims).toHaveBeenCalledOnce();
  expect(request.cookies.get("sb-session-auth-token")?.value).toBe("current-token");
  expect(response.cookies.getAll()).toEqual([]);
});

it("keeps fixture routes available without initiating an Auth client when Supabase is not configured", async () => {
  vi.stubEnv("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", "");

  const response = await updateSession(new NextRequest("https://onflows.test/"));

  expect(createServerClient).not.toHaveBeenCalled();
  expect(response.headers.get("x-middleware-next")).toBe("1");
  expect(response.cookies.getAll()).toEqual([]);
});
