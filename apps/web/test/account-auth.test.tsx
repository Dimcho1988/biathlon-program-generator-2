import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { renderToStaticMarkup } from "react-dom/server";
import { MagicLinkForm } from "../components/magic-link-form";
import { POST as saveProfile } from "../app/api/account/profile/route";
import { GET as completeAuthCallback } from "../app/auth/callback/route";
import { POST as logout } from "../app/api/auth/logout/route";
import { POST as sendMagicLink } from "../app/api/auth/magic-link/route";
import { supabaseAuthConfigured, supabasePublicConfig } from "../lib/supabase/config";
import { createClient } from "../lib/supabase/server";
import { currentAccountDisplayName } from "../lib/account-profile";

vi.mock("../lib/supabase/server", () => ({ createClient: vi.fn() }));

const request = (path: string, body?: FormData) => new Request(`https://web.example.test${path}`, {
  method: "POST",
  body,
});

const proxiedRequest = (path: string, body?: FormData) => new Request(`http://internal-render-host:10000${path}`, {
  method: "POST",
  headers: {
    "X-Forwarded-Host": "web.example.test",
    "X-Forwarded-Proto": "https",
  },
  body,
});

describe("onFlows account foundation", () => {
  afterEach(() => {
    vi.clearAllMocks();
    delete process.env.NEXT_PUBLIC_SUPABASE_URL;
    delete process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;
  });

  it("keeps fixture builds safe when Supabase Auth is not configured", () => {
    expect(supabaseAuthConfigured()).toBe(false);
    expect(() => supabasePublicConfig()).toThrow("Supabase Auth configuration is incomplete");
  });

  it("requires only the publishable browser key, never a service key", () => {
    process.env.NEXT_PUBLIC_SUPABASE_URL = "https://project.supabase.co";
    process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY = "sb_publishable_test";
    expect(supabasePublicConfig()).toEqual({
      url: "https://project.supabase.co",
      publishableKey: "sb_publishable_test",
    });
  });

  it("renders a passwordless email form", () => {
    const html = renderToStaticMarkup(<MagicLinkForm />);
    expect(html).toContain('type="email"');
    expect(html).toContain("Изпрати защитен линк");
    expect(html).not.toContain('type="password"');
  });

  it("explains that an invalid callback needs a new link", () => {
    const html = renderToStaticMarkup(<MagicLinkForm callbackError />);
    expect(html).toContain("Линкът е невалиден или е изтекъл");
    expect(html).toContain("Изпрати си нов линк");
  });

  it("sends magic links server-side with a same-origin callback", async () => {
    const signInWithOtp = vi.fn().mockResolvedValue({ error: null });
    vi.mocked(createClient).mockResolvedValue({ auth: { signInWithOtp } } as never);
    const response = await sendMagicLink(new Request("https://web.example.test/api/auth/magic-link", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Origin: "https://web.example.test",
      },
      body: JSON.stringify({ email: "athlete@example.test" }),
    }));

    expect(response.status).toBe(200);
    expect(createClient).toHaveBeenCalledWith({ requestTimeoutMs: 10_000 });
    expect(signInWithOtp).toHaveBeenCalledWith({
      email: "athlete@example.test",
      options: { emailRedirectTo: "https://web.example.test/auth/callback" },
    });
  });

  it("uses the public Render origin behind its reverse proxy", async () => {
    const signInWithOtp = vi.fn().mockResolvedValue({ error: null });
    vi.mocked(createClient).mockResolvedValue({ auth: { signInWithOtp } } as never);
    const response = await sendMagicLink(new Request("http://internal-render-host:10000/api/auth/magic-link", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Origin: "https://web.example.test",
        "X-Forwarded-Host": "web.example.test",
        "X-Forwarded-Proto": "https",
      },
      body: JSON.stringify({ email: "athlete@example.test" }),
    }));

    expect(response.status).toBe(200);
    expect(signInWithOtp).toHaveBeenCalledWith({
      email: "athlete@example.test",
      options: { emailRedirectTo: "https://web.example.test/auth/callback" },
    });
  });

  it("redirects failed callbacks to the public Render origin", async () => {
    const response = await completeAuthCallback(new NextRequest(
      "http://internal-render-host:10000/auth/callback",
      {
        headers: {
          "X-Forwarded-Host": "web.example.test",
          "X-Forwarded-Proto": "https",
        },
      },
    ));

    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe("https://web.example.test/login?error=callback");
  });

  it("rejects cross-origin magic-link requests before contacting Supabase", async () => {
    const response = await sendMagicLink(new Request("https://web.example.test/api/auth/magic-link", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Origin: "https://attacker.example.test",
      },
      body: JSON.stringify({ email: "athlete@example.test" }),
    }));

    expect(response.status).toBe(403);
    expect(createClient).not.toHaveBeenCalled();
  });

  it("creates a profile only for the cryptographically verified user id", async () => {
    const upsert = vi.fn().mockResolvedValue({ error: null });
    vi.mocked(createClient).mockResolvedValue({
      auth: { getClaims: vi.fn().mockResolvedValue({ data: { claims: { sub: "auth-user-1" } } }) },
      from: vi.fn().mockReturnValue({ upsert }),
    } as never);
    const body = new FormData();
    body.set("display_name", "  Димчо  ");
    body.set("user_id", "attacker-controlled-id");

    const response = await saveProfile(request("/api/account/profile", body));

    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe("https://web.example.test/account?saved=1");
    expect(upsert).toHaveBeenCalledWith(expect.objectContaining({
      user_id: "auth-user-1",
      display_name: "Димчо",
    }), { onConflict: "user_id" });
  });

  it("reads the verified account display name without treating it as an athlete alias", async () => {
    const maybeSingle = vi.fn().mockResolvedValue({ data: { display_name: "  Dimcho Mitsov  " } });
    const eq = vi.fn().mockReturnValue({ maybeSingle });
    const select = vi.fn().mockReturnValue({ eq });
    vi.mocked(createClient).mockResolvedValue({
      auth: { getClaims: vi.fn().mockResolvedValue({ data: { claims: { sub: "auth-user-1" } } }) },
      from: vi.fn().mockReturnValue({ select }),
    } as never);

    await expect(currentAccountDisplayName()).resolves.toBe("Dimcho Mitsov");
    expect(createClient).toHaveBeenCalledWith({ requestTimeoutMs: 5_000 });
    expect(eq).toHaveBeenCalledWith("user_id", "auth-user-1");
  });

  it("does not write a profile without a verified session", async () => {
    const from = vi.fn();
    vi.mocked(createClient).mockResolvedValue({
      auth: { getClaims: vi.fn().mockResolvedValue({ data: { claims: null } }) },
      from,
    } as never);
    const body = new FormData();
    body.set("display_name", "Test");

    const response = await saveProfile(request("/api/account/profile", body));

    expect(response.headers.get("location")).toBe("https://web.example.test/login");
    expect(from).not.toHaveBeenCalled();
  });

  it("redirects profile saves to the public Render origin", async () => {
    const upsert = vi.fn().mockResolvedValue({ error: null });
    vi.mocked(createClient).mockResolvedValue({
      auth: { getClaims: vi.fn().mockResolvedValue({ data: { claims: { sub: "auth-user-1" } } }) },
      from: vi.fn().mockReturnValue({ upsert }),
    } as never);
    const body = new FormData();
    body.set("display_name", "Dimcho Mitsov");

    const response = await saveProfile(proxiedRequest("/api/account/profile", body));

    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe("https://web.example.test/account?saved=1");
  });

  it("signs out through a POST-only route", async () => {
    const signOut = vi.fn().mockResolvedValue({ error: null });
    vi.mocked(createClient).mockResolvedValue({ auth: { signOut } } as never);

    const response = await logout(request("/api/auth/logout"));

    expect(signOut).toHaveBeenCalledOnce();
    expect(response.headers.get("location")).toBe("https://web.example.test/login");
  });

  it("redirects sign-out to the public Render origin", async () => {
    const signOut = vi.fn().mockResolvedValue({ error: null });
    vi.mocked(createClient).mockResolvedValue({ auth: { signOut } } as never);

    const response = await logout(proxiedRequest("/api/auth/logout"));

    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe("https://web.example.test/login");
  });
});
