import { afterEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MagicLinkForm } from "../components/magic-link-form";
import { POST as saveProfile } from "../app/api/account/profile/route";
import { POST as logout } from "../app/api/auth/logout/route";
import { supabaseAuthConfigured, supabasePublicConfig } from "../lib/supabase/config";
import { createClient } from "../lib/supabase/server";

vi.mock("../lib/supabase/server", () => ({ createClient: vi.fn() }));

const request = (path: string, body?: FormData) => new Request(`https://web.example.test${path}`, {
  method: "POST",
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

  it("signs out through a POST-only route", async () => {
    const signOut = vi.fn().mockResolvedValue({ error: null });
    vi.mocked(createClient).mockResolvedValue({ auth: { signOut } } as never);

    const response = await logout(request("/api/auth/logout"));

    expect(signOut).toHaveBeenCalledOnce();
    expect(response.headers.get("location")).toBe("https://web.example.test/login");
  });
});
