import { createHash, randomBytes } from "node:crypto";
import { accountRedirect, EMAIL_PATTERN, UUID_PATTERN, verifiedAccount } from "../../../../lib/account-route";

const inviteRoles = ["COACH", "ATHLETE"] as const;

export async function POST(request: Request) {
  const account = await verifiedAccount(request);
  if (!account.ok) return account.response;
  const form = await request.formData();
  const organizationId = String(form.get("organization_id") ?? "");
  const email = String(form.get("invitee_email") ?? "").trim().toLowerCase();
  const role = String(form.get("membership_role") ?? "");
  if (!UUID_PATTERN.test(organizationId) || !EMAIL_PATTERN.test(email) || email.length > 254 || !inviteRoles.includes(role as typeof inviteRoles[number]))
    return accountRedirect(request, "/account?error=invalid");
  const tokenHash = createHash("sha256").update(randomBytes(32)).digest("hex");
  const expiresAt = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString();
  const { error } = await account.supabase.from("onflows_connection_invites").insert({
    inviter_user_id: account.userId,
    invitee_email: email,
    token_hash: tokenHash,
    organization_id: organizationId,
    membership_role: role,
    status: "PENDING",
    expires_at: expiresAt,
  });
  if (!error) return accountRedirect(request, "/account?saved=invite");
  return accountRedirect(request, error.code === "23505" ? "/account?error=conflict" : "/account?error=access");
}
