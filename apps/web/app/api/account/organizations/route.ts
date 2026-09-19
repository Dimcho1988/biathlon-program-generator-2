import { randomBytes } from "node:crypto";
import { accountRedirect, verifiedAccount } from "../../../../lib/account-route";

export async function POST(request: Request) {
  const account = await verifiedAccount(request);
  if (!account.ok) return account.response;
  const form = await request.formData();
  const name = String(form.get("organization_name") ?? "").trim();
  if (name.length < 1 || name.length > 160)
    return accountRedirect(request, "/account?error=invalid");
  const slug = `team-${randomBytes(8).toString("hex")}`;
  const { error } = await account.supabase.rpc("create_onflows_organization", {
    p_name: name,
    p_slug: slug,
  });
  return accountRedirect(request, error ? "/account?error=save" : "/account?saved=organization");
}
