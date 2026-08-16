import { createHmac, timingSafeEqual } from "node:crypto";
import { cookies } from "next/headers";

export const ATHLETE_SESSION_COOKIE = "onflows-athlete-session";
const SESSION_SECONDS = 30 * 24 * 60 * 60;
const ALIAS_PATTERN = /^[a-z0-9][a-z0-9-]{2,63}$/;

export const multiProfileMode = () => process.env.ONFLOWS_PROFILE_MODE === "multi";

const sessionSecret = () => {
  const secret = process.env.ONFLOWS_SESSION_SECRET ?? "";
  if (secret.length < 32) throw new Error("Athlete session configuration is incomplete");
  return secret;
};

const signature = (value: string) =>
  createHmac("sha256", sessionSecret()).update(value).digest("base64url");

export function createAthleteSession(alias: string, now = Date.now()): string {
  if (!ALIAS_PATTERN.test(alias)) throw new Error("Athlete alias is invalid");
  const payload = `${alias}.${Math.floor(now / 1000) + SESSION_SECONDS}`;
  return `${payload}.${signature(payload)}`;
}

export function verifyAthleteSession(token: string | undefined, now = Date.now()): string | null {
  if (!token) return null;
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  const [alias, expiresAt, suppliedSignature] = parts;
  if (!ALIAS_PATTERN.test(alias) || !/^\d{10}$/.test(expiresAt)) return null;
  const payload = `${alias}.${expiresAt}`;
  const expected = Buffer.from(signature(payload));
  const supplied = Buffer.from(suppliedSignature);
  if (expected.length !== supplied.length || !timingSafeEqual(expected, supplied)) return null;
  if (Number(expiresAt) <= Math.floor(now / 1000)) return null;
  return alias;
}

export async function currentAthleteAlias(): Promise<string | null> {
  if (!multiProfileMode()) return null;
  const store = await cookies();
  return verifyAthleteSession(store.get(ATHLETE_SESSION_COOKIE)?.value);
}

export const athleteSessionCookie = (alias: string) => ({
  name: ATHLETE_SESSION_COOKIE,
  value: createAthleteSession(alias),
  httpOnly: true,
  secure: true,
  sameSite: "lax" as const,
  path: "/",
  maxAge: SESSION_SECONDS,
});
