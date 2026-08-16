import { NextResponse } from "next/server";
import { athleteSessionCookie } from "../../../../lib/athlete-session";

const ALIAS_PATTERN = /^[a-z0-9][a-z0-9-]{2,63}$/;

export async function GET(request: Request) {
  try {
    const ticket = new URL(request.url).searchParams.get("ticket") ?? "";
    const baseUrl = process.env.ONFLOWS_API_BASE_URL;
    const token = process.env.ONFLOWS_SERVICE_TOKEN;
    if (!baseUrl || !token || ticket.length < 32 || ticket.length > 128)
      throw new Error("Session handoff is invalid");
    const exchange = await fetch(new URL("/api/v2/session/exchange", baseUrl), {
      method: "POST",
      cache: "no-store",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ticket }),
      signal: AbortSignal.timeout(75_000),
    });
    if (!exchange.ok) throw new Error("Session exchange failed");
    const payload: unknown = await exchange.json();
    const alias = typeof payload === "object" && payload !== null && "athlete_alias" in payload
      ? payload.athlete_alias
      : null;
    if (typeof alias !== "string" || !ALIAS_PATTERN.test(alias))
      throw new Error("Session exchange response is invalid");
    const response = new NextResponse(null, {
      status: 303,
      headers: { Location: "/?intervals=connected" },
    });
    response.cookies.set(athleteSessionCookie(alias));
    return response;
  } catch {
    return new NextResponse(null, {
      status: 303,
      headers: { Location: "/?intervals=connect-error" },
    });
  }
}
