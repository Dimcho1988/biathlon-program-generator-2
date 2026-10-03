import { afterEach, describe, expect, it, vi } from "vitest";
import { POST as login } from "../app/api/auth/password/route";
import { POST as savePassword } from "../app/api/account/password/route";
import { recentlyAuthenticated, validNewPassword } from "../lib/password-auth";
import { createClient } from "../lib/supabase/server";

vi.mock("../lib/supabase/server", () => ({ createClient: vi.fn() }));
const password = "Synthetic-Test-Password!";
const request = (path: string, body: unknown, origin = "https://web.example.test") => new Request(`https://web.example.test${path}`, {
  method: "POST", headers: { Origin: origin, "Content-Type": "application/json" }, body: JSON.stringify(body),
});
const settingRequest = (body: unknown = { password }) => request("/api/account/password", body);
function authenticated({ error = null, claims = { sub: "verified-user", amr: [{ method: "otp", timestamp: Math.floor(Date.now() / 1000) }] }, user = { id: "verified-user", email_confirmed_at: "2026-10-03" } }: { error?: unknown; claims?: unknown; user?: unknown } = {}) {
  const auth = {
    getUser: vi.fn().mockResolvedValue({ data: { user }, error: null }),
    getClaims: vi.fn().mockResolvedValue({ data: { claims }, error: null }),
    updateUser: vi.fn().mockResolvedValue({ error }),
  };
  vi.mocked(createClient).mockResolvedValue({ auth } as never);
  return auth;
}
afterEach(() => vi.clearAllMocks());

describe("password input and recent authentication", () => {
  it("keeps passwords literal and bounds the UTF-8 bytes hashed by bcrypt", () => {
    expect(validNewPassword("a".repeat(12))).toBe(true);
    expect(validNewPassword("a".repeat(72))).toBe(true);
    expect(validNewPassword("я".repeat(36))).toBe(true);
    expect(validNewPassword("я".repeat(37))).toBe(false);
    expect(validNewPassword("a".repeat(73))).toBe(false);
    expect(validNewPassword("short")).toBe(false);
    expect(validNewPassword({ password })).toBe(false);
  });
  it("requires an actual credential login within24h, not a fresh JWT/refresh or anonymous AMR", () => {
    const now = Date.UTC(2026, 9, 3);
    const seconds = now / 1000;
    const valid = { amr: [{ method: "otp", timestamp: seconds - 60 }] };
    expect(recentlyAuthenticated(valid, now)).toBe(true);
    expect(recentlyAuthenticated({ amr: [{ method: "password", timestamp: seconds - 86_400 }] }, now)).toBe(false);
    expect(recentlyAuthenticated({ iat: seconds, amr: [{ method: "password", timestamp: seconds - 90_000 }, { method: "token_refresh", timestamp: seconds }] }, now)).toBe(false);
    for (const method of ["anonymous", "token_refresh", "unknown", undefined]) {
      expect(recentlyAuthenticated({ amr: [{ method, timestamp: seconds }] }, now)).toBe(false);
    }
    expect(recentlyAuthenticated({ amr: [{ method: "otp", timestamp: seconds + 3600 }] }, now)).toBe(false);
    expect(recentlyAuthenticated({ amr: ["otp"] }, now)).toBe(false);
    expect(recentlyAuthenticated({}, now)).toBe(false);
  });
});

describe("password sign-in", () => {
  it("sets a Supabase SSR session without returning any tokens/password and never changes identities", async () => {
    const signInWithPassword = vi.fn().mockResolvedValue({ error: null, data: { session: { access_token: "not-returned" } } });
    vi.mocked(createClient).mockResolvedValue({ auth: { signInWithPassword } } as never);
    const response = await login(request("/api/auth/password", { email: " athlete@example.test ", password: " literal password " }));
    expect(signInWithPassword).toHaveBeenCalledWith({ email: "athlete@example.test", password: " literal password " });
    expect(createClient).toHaveBeenCalledWith({ requestTimeoutMs: 10_000 });
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ ok: true });
    expect(response.headers.get("cache-control")).toBe("private, no-store");
  });
  it("rejects cross-origin and malformed credentials without a provider call", async () => {
    expect((await login(request("/api/auth/password", { email: "athlete@example.test", password }, "https://attacker.test"))).status).toBe(403);
    for (const body of [{ email: "invalid", password }, { email: "athlete@example.test", password: "" }, { email: "athlete@example.test", password: "a".repeat(1025) }, null]) {
      expect((await login(request("/api/auth/password", body))).status).toBe(400);
    }
    expect(createClient).not.toHaveBeenCalled();
  });
  it("has generic failure responses and preserves provider limits/temporary failures", async () => {
    const signInWithPassword = vi.fn();
    vi.mocked(createClient).mockResolvedValue({ auth: { signInWithPassword } } as never);
    for (const [error, status] of [[{ status: 400, message: "unknown user" }, 401], [{ status: 429 }, 429], [{ status: 503 }, 502]] as const) {
      signInWithPassword.mockResolvedValue({ error });
      const response = await login(request("/api/auth/password", { email: "athlete@example.test", password }));
      expect(response.status).toBe(status);
      expect(await response.json()).toEqual({ ok: false });
    }
    signInWithPassword.mockRejectedValue(new Error("provider unavailable"));
    expect((await login(request("/api/auth/password", { email: "athlete@example.test", password }))).status).toBe(502);
  });
  it("uses the public Render origin", async () => {
    const signInWithPassword = vi.fn().mockResolvedValue({ error: null });
    vi.mocked(createClient).mockResolvedValue({ auth: { signInWithPassword } } as never);
    const response = await login(new Request("http://internal:10000/api/auth/password", {
      method: "POST", headers: { Origin: "https://web.example.test", "X-Forwarded-Host": "web.example.test", "X-Forwarded-Proto": "https" }, body: JSON.stringify({ email: "athlete@example.test", password }),
    }));
    expect(response.status).toBe(200);
  });
});

describe("authenticated password setup/change", () => {
  it("changes only the live-verified authenticated user, ignoring an attacker-supplied user ID", async () => {
    const auth = authenticated();
    const response = await savePassword(settingRequest({ password, user_id: "other-user", currentPassword: "old password" }));
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ ok: true });
    expect(auth.getUser).toHaveBeenCalledOnce();
    expect(auth.getClaims).toHaveBeenCalledOnce();
    expect(auth.updateUser).toHaveBeenCalledWith({ password, current_password: "old password" });
    expect(response.headers.get("cache-control")).toBe("private, no-store");
  });
  it("requires same-origin, bounded new password and optional bounded current password", async () => {
    const auth = authenticated();
    expect((await savePassword(request("/api/account/password", { password }, "https://attacker.test"))).status).toBe(403);
    for (const body of [{ password: "short" }, { password: "я".repeat(37) }, { password, currentPassword: 10 }, { password, currentPassword: "a".repeat(1025) }, null]) {
      expect((await savePassword(settingRequest(body))).status).toBe(400);
    }
    expect(auth.getUser).not.toHaveBeenCalled();
    expect(auth.updateUser).not.toHaveBeenCalled();
  });
  it("denies revoked/unconfirmed/mismatched/stale sessions without changing the password", async () => {
    for (const scenario of [{ user: null }, { user: { id: "verified-user" } }, { claims: { sub: "other-user", amr: [{ method: "otp", timestamp: Date.now() / 1000 }] } }, { claims: { sub: "verified-user", iat: Date.now() / 1000, amr: [{ method: "token_refresh", timestamp: Date.now() / 1000 }] } }]) {
      const auth = authenticated(scenario);
      const response = await savePassword(settingRequest());
      expect(response.status).toBe(401);
      expect(await response.json()).toEqual({ ok: false, reason: "reauthenticate" });
      expect(auth.updateUser).not.toHaveBeenCalled();
    }
    const auth = authenticated();
    auth.getUser.mockResolvedValue({ data: { user: null }, error: { status: 401 } });
    expect((await savePassword(settingRequest())).status).toBe(401);
    expect(auth.updateUser).not.toHaveBeenCalled();
  });
  it("omits a blank current password for first setup and respects provider security errors", async () => {
    const auth = authenticated();
    expect((await savePassword(settingRequest({ password, currentPassword: "" }))).status).toBe(200);
    expect(auth.updateUser).toHaveBeenCalledWith({ password });
    for (const [code, status, reason] of [["weak_password",400,"weak"], ["same_password",400,"same"], ["reauthentication_needed",401,"reauthenticate"], ["current_password_required",400,"current-password"]] as const) {
      auth.updateUser.mockResolvedValue({ error: { code } });
      const response = await savePassword(settingRequest());
      expect(response.status).toBe(status);
      expect(await response.json()).toEqual({ ok: false, reason });
    }
  });
});
