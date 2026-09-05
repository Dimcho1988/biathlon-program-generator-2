import { NextResponse } from "next/server";
import { publicOrigin } from "../../../../lib/public-origin";
import { createClient } from "../../../../lib/supabase/server";

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export async function POST(request: Request) {
  const origin = request.headers.get("origin");
  const callbackOrigin = publicOrigin(request);
  if (!origin || origin !== callbackOrigin) {
    return NextResponse.json({ ok: false }, { status: 403 });
  }

  let email = "";
  try {
    const body = await request.json() as { email?: unknown };
    email = typeof body.email === "string" ? body.email.trim() : "";
  } catch {
    return NextResponse.json({ ok: false }, { status: 400 });
  }

  if (!EMAIL_PATTERN.test(email) || email.length > 254) {
    return NextResponse.json({ ok: false }, { status: 400 });
  }

  const supabase = await createClient({ requestTimeoutMs: 10_000 });
  const { error } = await supabase.auth.signInWithOtp({
    email,
    options: {
      emailRedirectTo: `${callbackOrigin}/auth/callback`,
    },
  });

  if (error) {
    return NextResponse.json(
      { ok: false },
      { status: error.status === 429 ? 429 : 502 },
    );
  }

  return NextResponse.json({ ok: true });
}
