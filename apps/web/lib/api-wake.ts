import { rememberedNavigationHref } from "./dashboard-navigation";

export const API_WAKE_RETRY_KEY = "onflows-api-wake-retry-v2";
export const API_WAKE_RETURN_KEY = "onflows-api-wake-return";
export const API_RATE_LIMIT_MESSAGE = "Услугата за данни временно ограничава заявките при стартиране (429).";

export function renderWakeHref(baseUrl?: string): string | undefined {
  if (!baseUrl) return undefined;
  try {
    const url = new URL(baseUrl);
    if (url.protocol !== "https:" || !url.hostname.endsWith(".onrender.com") || url.username || url.password) return undefined;
    return new URL("/api/v2/wake", url.origin).toString();
  } catch { return undefined; }
}

// Tab-local navigation only. Never send the return path, identity or filters to
// the public wake endpoint, and never follow an external URL from storage.
export function wakeReturnHref(saved: string | null): string {
  try {
    const url = new URL(saved ?? "/", "https://onflows.invalid");
    if (url.origin !== "https://onflows.invalid") return "/";
    const path = url.pathname;
    if (/^\/activities\/[A-Za-z0-9_-]+(?:\/shadow)?$/.test(path)) return path;
    if (!["/", "/activities", "/trainability", "/response", "/speed", "/planning"].includes(path)) return "/";
    const target = new URL(rememberedNavigationHref(path, saved), url.origin);
    if (path === "/" && url.searchParams.get("settings") === "edit") target.searchParams.set("settings", "edit");
    return `${target.pathname}${target.search}`;
  } catch { return "/"; }
}
