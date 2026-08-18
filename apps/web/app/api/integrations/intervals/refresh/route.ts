import { NextResponse } from "next/server";
import { currentAthleteAlias, multiProfileMode } from "../../../../../lib/athlete-session";
import { waitForApi } from "../../../../../lib/api-readiness";

async function refreshIntervalsData() {
  try {
    const athleteAlias = await currentAthleteAlias();
    if (multiProfileMode() && !athleteAlias)
      return new NextResponse(null, { status: 303, headers: { Location: "/?intervals=session-required" } });
    const baseUrl = process.env.ONFLOWS_API_BASE_URL;
    const token = process.env.ONFLOWS_SERVICE_TOKEN;
    if (!baseUrl || !token) throw new Error("Server integration configuration is incomplete");
    await waitForApi(baseUrl);
    const response = await fetch(new URL("/api/v2/real/refresh", baseUrl), {
      method: "POST",
      cache: "no-store",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/json",
        ...(athleteAlias ? { "X-OnFlows-Athlete-Alias": athleteAlias } : {}),
      },
      signal: AbortSignal.timeout(180_000),
    });
    if (!response.ok) throw new Error("Refresh failed");
    return new NextResponse(null, { status: 303, headers: { Location: "/" } });
  } catch {
    return new NextResponse(null, { status: 303, headers: { Location: "/?intervals=refresh-error" } });
  }
}

export const POST = refreshIntervalsData;

// Some browsers can replay a form target as a navigation after a deployment or
// history restore. Refresh is an authenticated, read-only Intervals import, so
// route the fallback navigation through the same session-scoped handler instead of
// exposing a raw 405 page.
export const GET = refreshIntervalsData;
