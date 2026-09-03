import { NextResponse } from "next/server";
import { currentAuthorizedAthlete } from "../../../../lib/account-access";
import { multiProfileMode } from "../../../../lib/athlete-session";
import { waitForApi } from "../../../../lib/api-readiness";

const fieldNames = ["z1_low", "z2_low", "z3_low", "z4_low", "z5_low", "z5_high"] as const;

export async function POST(request: Request) {
  let stage = "session";
  try {
    const athlete = await currentAuthorizedAthlete();
    const athleteAlias = athlete?.athleteAlias;
    if (!multiProfileMode() || !athleteAlias)
      return new NextResponse(null, { status: 303, headers: { Location: "/?intervals=session-required" } });
    if (!athlete.canEditPlan)
      return new NextResponse(null, { status: 303, headers: { Location: "/?settings=forbidden" } });
    const baseUrl = process.env.ONFLOWS_API_BASE_URL;
    const token = process.env.ONFLOWS_SERVICE_TOKEN;
    if (!baseUrl || !token) throw new Error("Server integration configuration is incomplete");
    stage = "form";
    const form = await request.formData();
    const bounds = fieldNames.map((name) => Number(form.get(name)));
    const timezone = String(form.get("timezone") ?? "").trim();
    const hrmax = Number(form.get("hrmax_bpm"));
    if (
      bounds.some((value) => !Number.isInteger(value) || value < 30 || value > 240)
      || bounds.some((value, index) => index > 0 && bounds[index - 1] >= value)
      || !timezone
      || !Number.isInteger(hrmax) || hrmax < 30 || hrmax > 240
      || bounds[5] > hrmax
    ) return new NextResponse(null, { status: 303, headers: { Location: "/?settings=invalid" } });
    stage = "api";
    await waitForApi(baseUrl);
    const response = await fetch(new URL("/api/v2/athlete/settings", baseUrl), {
      method: "PUT",
      cache: "no-store",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-OnFlows-Athlete-Alias": athleteAlias,
      },
      body: JSON.stringify({ hr_zone_bounds_bpm: bounds, timezone, hrmax_bpm: hrmax }),
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
