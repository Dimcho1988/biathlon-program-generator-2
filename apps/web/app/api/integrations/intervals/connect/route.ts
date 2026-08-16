import { NextResponse } from "next/server";
import { currentAthleteAlias } from "../../../../../lib/athlete-session";

const apiConfiguration = () => {
  const baseUrl = process.env.ONFLOWS_API_BASE_URL;
  const token = process.env.ONFLOWS_SERVICE_TOKEN;
  if (!baseUrl || !token) throw new Error("Server integration configuration is incomplete");
  return { baseUrl, token };
};

export async function GET() {
  try {
    const { baseUrl, token } = apiConfiguration();
    const athleteAlias = await currentAthleteAlias();
    const response = await fetch(new URL("/api/v2/integrations/intervals/authorize", baseUrl), {
      method: "POST",
      cache: "no-store",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/json",
        ...(athleteAlias ? { "X-OnFlows-Athlete-Alias": athleteAlias } : {}),
      },
      // The preview API runs on Render Free and may need 50+ seconds to wake.
      signal: AbortSignal.timeout(75_000),
    });
    if (!response.ok) throw new Error("OAuth start failed");
    const payload: unknown = await response.json();
    if (typeof payload !== "object" || payload === null || !("authorization_url" in payload) || typeof payload.authorization_url !== "string")
      throw new Error("OAuth response is invalid");
    const destination = new URL(payload.authorization_url);
    if (destination.protocol !== "https:" || destination.hostname !== "intervals.icu" || destination.pathname !== "/oauth/authorize")
      throw new Error("OAuth destination is invalid");
    return NextResponse.redirect(destination, 303);
  } catch {
    console.error("Intervals OAuth start failed");
    return new NextResponse(null, { status: 303, headers: { Location: "/?intervals=connect-start-error" } });
  }
}
