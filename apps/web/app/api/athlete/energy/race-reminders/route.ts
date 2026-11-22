import { NextResponse } from "next/server";
import { currentAuthorizedAthlete } from "../../../../../lib/account-access";

export async function GET() {
  const a = await currentAuthorizedAthlete();
  if (!a) return NextResponse.json({ events: [] }, { status: 401 });
  if (!a.canViewRecovery || !(a.isOwner || a.canViewPlan))
    return NextResponse.json({ events: [] }, { status: 403 });
  try {
    const base = process.env.ONFLOWS_API_BASE_URL, token = process.env.ONFLOWS_SERVICE_TOKEN;
    if (!base || !token) throw new Error("Missing configuration");
    const response = await fetch(new URL("/api/v2/athlete/energy/race-reminders", base), {
      cache: "no-store", headers: { Authorization: `Bearer ${token}`, "X-OnFlows-Athlete-Alias": a.athleteAlias },
      signal: AbortSignal.timeout(10000),
    });
    if (!response.ok) throw new Error("Unavailable");
    return NextResponse.json(await response.json(), { headers: { "Cache-Control": "private, no-store" } });
  } catch { return NextResponse.json({ events: [] }, { status: 503 }); }
}
