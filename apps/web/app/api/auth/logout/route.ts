import { NextResponse } from "next/server";
import { ATHLETE_SESSION_COOKIE } from "../../../../lib/athlete-session";
import { publicOrigin } from "../../../../lib/public-origin";
import { createClient } from "../../../../lib/supabase/server";

export async function POST(request: Request) {
  const supabase = await createClient();
  await supabase.auth.signOut();
  const response = NextResponse.redirect(new URL("/login", publicOrigin(request)), 303);
  response.cookies.set({
    name: ATHLETE_SESSION_COOKIE,
    value: "",
    httpOnly: true,
    secure: true,
    sameSite: "lax",
    path: "/",
    maxAge: 0,
  });
  return response;
}
