import { NextResponse } from "next/server";
import { publicOrigin } from "../../../../lib/public-origin";
import { recentlyAuthenticated, validNewPassword } from "../../../../lib/password-auth";
import { createClient } from "../../../../lib/supabase/server";

const reply = (status: number, reason?: string) => NextResponse.json({ ok: status === 200, ...(reason ? { reason } : {}) }, {
  status, headers: { "Cache-Control": "private, no-store" },
});

export async function POST(request: Request) {
  if (request.headers.get("origin") !== publicOrigin(request)) return reply(403);
  let password: unknown;
  let currentPassword: unknown;
  try {
    const body = await request.json() as { password?: unknown; currentPassword?: unknown };
    password = body.password;
    currentPassword = body.currentPassword;
  } catch { return reply(400, "invalid"); }
  if (!validNewPassword(password) || (currentPassword !== undefined
    && (typeof currentPassword !== "string" || currentPassword.length > 1024))) return reply(400, "invalid");

  try {
    const supabase = await createClient({ requestTimeoutMs: 10_000 });
    // Live verification protects this sensitive change from a revoked session.
    const { data: { user }, error: userError } = await supabase.auth.getUser();
    if (userError || !user?.email_confirmed_at) return reply(401, "reauthenticate");
    const { data, error: claimsError } = await supabase.auth.getClaims();
    if (claimsError || !data?.claims || data.claims.sub !== user.id
      || !recentlyAuthenticated(data.claims)) return reply(401, "reauthenticate");

    // Only the authenticated user's password can be changed; no submitted ID is used.
    const { error } = await supabase.auth.updateUser({ password, ...(currentPassword ? { current_password: currentPassword } : {}) });
    if (!error) return reply(200);
    if (error.status === 429) return reply(429, "rate-limited");
    if (error.code === "reauthentication_needed" || error.code === "session_not_found") return reply(401, "reauthenticate");
    if (error.code === "weak_password") return reply(400, "weak");
    if (error.code === "same_password") return reply(400, "same");
    if (error.code === "current_password_required" || error.code === "current_password_mismatch"
      || error.code === "invalid_credentials") return reply(400, "current-password");
    return reply(502, "unavailable");
  } catch { return reply(502, "unavailable"); }
}
