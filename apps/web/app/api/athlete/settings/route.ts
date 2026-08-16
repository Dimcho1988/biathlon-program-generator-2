import { NextResponse } from "next/server";
import { currentAthleteAlias, multiProfileMode } from "../../../../lib/athlete-session";

const fieldNames = ["z1_low", "z2_low", "z3_low", "z4_low", "z5_low", "z5_high"] as const;

export async function POST(request: Request) {
  let stage = "session";
  try {
    const athleteAlias = await currentAthleteAlias();
    if (!multiProfileMode() || !athleteAlias)
      return new NextResponse(null, { status: 303, headers: { Location: "/?intervals=session-required" } });
    const baseUrl = process.env.ONFLOWS_API_BASE_URL;
    const token = process.env.ONFLOWS_SERVICE_TOKEN;
    if (!baseUrl || !token) throw new Error("Server integration configuration is incomplete");
    stage = "form";
    const form = await request.formData();
    const bounds = fieldNames.map((name) => Number(form.get(name)));
    const timezone = String(form.get("timezone") ?? "").trim();
    if (
      bounds.some((value) => !Number.isInteger(value) || value < 30 || value > 240)
      || bounds.some((value, index) => index > 0 && bounds[index - 1] >= value)
      || !timezone
    ) return new NextResponse(null, { status: 303, headers: { Location: "/?settings=invalid" } });
    stage = "api";
    const response = await fetch(new URL("/api/v2/athlete/settings", baseUrl), {
      method: "PUT",
      cache: "no-store",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-OnFlows-Athlete-Alias": athleteAlias,
      },
      body: JSON.stringify({ hr_zone_bounds_bpm: bounds, timezone }),
      signal: AbortSignal.timeout(75_000),
    });
    stage = `api-response-${response.status}`;
    if (!response.ok) throw new Error("Athlete settings update failed");
    return new NextResponse(null, { status: 303, headers: { Location: "/?settings=saved" } });
  } catch {
    console.error(`Athlete settings update failed [${stage}]`);
    return new NextResponse(null, { status: 303, headers: { Location: "/?settings=error" } });
  }
}
