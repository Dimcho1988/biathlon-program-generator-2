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
    const source = String(form.get("hr_zone_source") ?? "MANUAL");
    const bounds = fieldNames.map((name) => Number(form.get(name)));
    const timezone = String(form.get("timezone") ?? "").trim();
    const maximumText = String(form.get("hrmax_bpm") ?? "").trim();
    const hrmax = maximumText ? Number(maximumText) : null;
    const ageText = String(form.get("age_years") ?? "").trim();
    const age = ageText ? Number(ageText) : null;
    const indicator = String(form.get("leading_indicator") ?? "HEART_RATE");
    const resolvedMaximum = hrmax ?? (age !== null ? Math.floor(208 - .7 * age + .5) : null);
    if (
      !["MANUAL", "AUTOMATIC_HRMAX"].includes(source)
      || (source === "MANUAL" && (
        bounds.some((value) => !Number.isInteger(value) || value < 30 || value > 240)
        || bounds.some((value, index) => index > 0 && bounds[index - 1] >= value)
        || (resolvedMaximum !== null && bounds[5] > resolvedMaximum)
      ))
      || !timezone
      || (hrmax !== null && (!Number.isInteger(hrmax) || hrmax < 30 || hrmax > 240))
      || (age !== null && (!Number.isInteger(age) || age < 5 || age > 100))
      || resolvedMaximum === null
      || !["HEART_RATE", "SPEED"].includes(indicator)
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
      body: JSON.stringify({ hr_zone_bounds_bpm: source === "MANUAL" ? bounds : null, timezone, hrmax_bpm: hrmax, hr_zone_source: source,
        age_years: age, leading_indicator: indicator }),
      signal: AbortSignal.timeout(75_000),
    });
    stage = `api-response-${response.status}`;
    if (response.status === 422)
      return new NextResponse(null, { status: 303, headers: { Location: "/?settings=invalid" } });
    if (!response.ok) throw new Error("Athlete settings update failed");
    return new NextResponse(null, { status: 303, headers: { Location: "/?settings=saved" } });
  } catch {
    console.error(`Athlete settings update failed [${stage}]`);
    return new NextResponse(null, { status: 303, headers: { Location: "/?settings=error" } });
  }
}
