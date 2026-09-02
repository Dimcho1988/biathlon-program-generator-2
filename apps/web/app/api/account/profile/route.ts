import { NextResponse } from "next/server";
import { createClient } from "../../../../lib/supabase/server";

export async function POST(request: Request) {
  const supabase = await createClient();
  const { data } = await supabase.auth.getClaims();
  const userId = data?.claims?.sub;
  if (!userId) return NextResponse.redirect(new URL("/login", request.url), 303);
  const form = await request.formData();
  const displayName = String(form.get("display_name") ?? "").trim();
  if (displayName.length < 1 || displayName.length > 100)
    return NextResponse.redirect(new URL("/account?error=invalid", request.url), 303);
  const { error } = await supabase.from("onflows_profiles").upsert({
    user_id: userId,
    display_name: displayName,
    updated_at: new Date().toISOString(),
  }, { onConflict: "user_id" });
  return NextResponse.redirect(new URL(error ? "/account?error=save" : "/account?saved=1", request.url), 303);
}
