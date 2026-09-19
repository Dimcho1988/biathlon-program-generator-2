import { accountRedirect, UUID_PATTERN, verifiedAccount } from "../../../../lib/account-route";

export async function POST(request: Request) {
  const account = await verifiedAccount(request);
  if (!account.ok) return account.response;
  const form = await request.formData();
  const organizationId = String(form.get("organization_id") ?? "");
  const coachUserId = String(form.get("coach_user_id") ?? "");
  const athleteUserId = String(form.get("athlete_user_id") ?? "");
  if (![organizationId, coachUserId, athleteUserId].every((value) => UUID_PATTERN.test(value)) || coachUserId === athleteUserId)
    return accountRedirect(request, "/account?error=invalid");
  const { error } = await account.supabase.from("onflows_coach_athlete_assignments").upsert({
    organization_id: organizationId,
    coach_user_id: coachUserId,
    athlete_user_id: athleteUserId,
    assigned_by_user_id: account.userId,
    can_edit_plan: form.get("can_edit_plan") === "on",
  }, { onConflict: "organization_id,coach_user_id,athlete_user_id" });
  return accountRedirect(request, error ? "/account?error=access" : "/account?saved=assignment");
}
