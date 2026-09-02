import { NextResponse } from "next/server";
import { publicOrigin } from "../../../../lib/public-origin";
import { createClient } from "../../../../lib/supabase/server";

export async function POST(request: Request) {
  const supabase = await createClient();
  await supabase.auth.signOut();
  return NextResponse.redirect(new URL("/login", publicOrigin(request)), 303);
}
