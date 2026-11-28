import { NextResponse } from "next/server";
import { publicOrigin } from "../../../../lib/public-origin";
import { createClient } from "../../../../lib/supabase/server";

const reply = (status: number) => NextResponse.json({ ok: status === 200 }, {
  status, headers: { "Cache-Control": "private, no-store" },
});

export async function POST(request: Request) {
  if (request.headers.get("origin") !== publicOrigin(request)) return reply(403);
  let email: string;
  let password: string;
  try {
    const body = await request.json() as { email?: unknown; password?: unknown };
    email = typeof body.email === "string" ? body.email.trim() : "";
    password = typeof body.password === "string" ? body.password : "";
  } catch { return reply(400); }
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) || email.length > 254
    || !password || password.length > 1024) return reply(400);

  try {
    const supabase = await createClient({ requestTimeoutMs: 10_000 });
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) return reply(error.status === 429 ? 429 : error.status && error.status >= 500 ? 502 : 401);
    return reply(200);
  } catch { return reply(502); }
}
