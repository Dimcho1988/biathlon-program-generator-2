import { NextResponse } from "next/server";
import { publicOrigin } from "./public-origin";
import { createClient } from "./supabase/server";

export const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
export const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
export const ATHLETE_ALIAS_PATTERN = /^[a-z0-9][a-z0-9-]{2,63}$/;

export function accountRedirect(request: Request, destination: string) {
  return NextResponse.redirect(new URL(destination, publicOrigin(request)), 303);
}

export function isSameOrigin(request: Request) {
  const origin = request.headers.get("origin");
  return origin !== null && origin === publicOrigin(request);
}

export async function verifiedAccount(request: Request) {
  if (!isSameOrigin(request)) return { ok: false, response: NextResponse.json({ ok: false }, { status: 403 }) } as const;
  const supabase = await createClient({ requestTimeoutMs: 10_000 });
  const { data } = await supabase.auth.getClaims();
  const userId = data?.claims?.sub;
  if (!userId) return { ok: false, response: accountRedirect(request, "/login") } as const;
  return { ok: true, supabase, userId } as const;
}
