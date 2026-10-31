export const MIN_PASSWORD_LENGTH = 12;
export const MAX_PASSWORD_BYTES = 72;
const RECENT_LOGIN_SECONDS = 24 * 60 * 60;

export function validNewPassword(password: unknown): password is string {
  return typeof password === "string"
    && [...password].length >= MIN_PASSWORD_LENGTH
    && new TextEncoder().encode(password).length <= MAX_PASSWORD_BYTES;
}

// JWT issue time changes on refresh. AMR records the actual authentication time.
export function recentlyAuthenticated(claims: Record<string, unknown>, now = Date.now()): boolean {
  if (!Array.isArray(claims.amr)) return false;
  const seconds = Math.floor(now / 1000);
  return claims.amr.some((entry: unknown) => {
    if (!entry || typeof entry !== "object") return false;
    const { timestamp, method } = entry as { timestamp?: unknown; method?: unknown };
    return (method === "password" || method === "otp" || method === "magiclink" || method === "recovery" || method === "email/signup")
      && typeof timestamp === "number" && Number.isFinite(timestamp)
      && timestamp <= seconds + 60 && timestamp > seconds - RECENT_LOGIN_SECONDS;
  });
}
