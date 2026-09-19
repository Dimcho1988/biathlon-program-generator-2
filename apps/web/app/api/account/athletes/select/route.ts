import { NextResponse } from "next/server";
import { accountRedirect, ATHLETE_ALIAS_PATTERN, verifiedAccount } from "../../../../../lib/account-route";
import { athleteSessionCookie } from "../../../../../lib/athlete-session";
import { publicOrigin } from "../../../../../lib/public-origin";

export async function POST(request: Request) {
  const account = await verifiedAccount(request);
  if (!account.ok) return account.response;
  const form = await request.formData();
  const alias = String(form.get("athlete_alias") ?? "");
  if (!ATHLETE_ALIAS_PATTERN.test(alias)) return accountRedirect(request, "/account?error=invalid");
  const { data, error } = await account.supabase.from("onflows_user_athletes")
    .select("athlete_alias")
    .eq("athlete_alias", alias)
    .maybeSingle<{ athlete_alias: string }>();
  if (error || !data) return accountRedirect(request, "/account?error=access");
  const response = NextResponse.redirect(new URL("/?profile=selected", publicOrigin(request)), 303);
  response.cookies.set(athleteSessionCookie(alias));
  return response;
}
