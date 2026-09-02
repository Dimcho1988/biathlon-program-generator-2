import { createClient } from "./supabase/server";

type AccountProfile = { display_name: string };

export async function currentAccountDisplayName(): Promise<string | null> {
  try {
    const supabase = await createClient({ requestTimeoutMs: 5_000 });
    const { data } = await supabase.auth.getClaims();
    const userId = data?.claims?.sub;
    if (!userId) return null;
    const { data: profile } = await supabase.from("onflows_profiles")
      .select("display_name")
      .eq("user_id", userId)
      .maybeSingle<AccountProfile>();
    const displayName = profile?.display_name?.trim();
    return displayName || null;
  } catch {
    // The training snapshot remains usable if account metadata is temporarily unavailable.
    return null;
  }
}
