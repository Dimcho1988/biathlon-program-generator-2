import { createBrowserClient } from "@supabase/ssr";
import { supabasePublicConfig } from "./config";

export function createClient() {
  const { url, publishableKey } = supabasePublicConfig();
  return createBrowserClient(url, publishableKey);
}
