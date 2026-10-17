import { accountRedirect, UUID_PATTERN, verifiedAccount } from "../../../../../lib/account-route";

export async function POST(request: Request) {
  const account = await verifiedAccount(request);
  if (!account.ok) return account.response;
  const form = await request.formData();
  const inviteId = String(form.get("invite_id") ?? "");
  if (!UUID_PATTERN.test(inviteId)) return accountRedirect(request, "/account?error=invalid");
  const { error } = await account.supabase.rpc("accept_onflows_organization_invite", {
    p_invite_id: inviteId,
  });
  return accountRedirect(request, error ? "/account?error=access" : "/account?saved=accepted");
}
