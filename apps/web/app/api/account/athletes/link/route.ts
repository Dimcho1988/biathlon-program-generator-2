import { accountRedirect, ATHLETE_ALIAS_PATTERN, verifiedAccount } from "../../../../../lib/account-route";
import { currentAthleteAlias } from "../../../../../lib/athlete-session";

export async function POST(request: Request) {
  const account = await verifiedAccount(request);
  if (!account.ok) return account.response;
  const form = await request.formData();
  const alias = String(form.get("athlete_alias") ?? "");
  const signedAlias = await currentAthleteAlias();
  if (!ATHLETE_ALIAS_PATTERN.test(alias) || alias !== signedAlias)
    return accountRedirect(request, "/account?error=access");
  const { error } = await account.supabase.from("onflows_user_athletes").upsert({
    user_id: account.userId,
    athlete_alias: alias,
    is_owner: true,
  }, { onConflict: "user_id,athlete_alias" });
  if (!error) return accountRedirect(request, "/account?saved=linked");
  return accountRedirect(request, error.code === "23505" ? "/account?error=conflict" : "/account?error=save");
}
