import { NextResponse } from "next/server";
import { currentAuthorizedAthlete } from "../../../../lib/account-access";
import { multiProfileMode } from "../../../../lib/athlete-session";
import { waitForApi } from "../../../../lib/api-readiness";
import { parsePlanningCalendarInput } from "../../../../lib/planning-calendar";

const redirect = (state: "calendar-saved" | "calendar-invalid" | "calendar-error") =>
  new NextResponse(null, {
    status: 303,
    headers: { Location: `/planning?planning=${state}` },
  });

export async function POST(request: Request) {
  let stage = "session";
  try {
    const athlete = await currentAuthorizedAthlete();
    const athleteAlias = athlete?.athleteAlias;
    if (!multiProfileMode() || !athleteAlias)
      return new NextResponse(null, {
        status: 303,
        headers: { Location: "/?intervals=session-required" },
      });
    if (!athlete.canEditPlan)
      return new NextResponse(null, { status: 303, headers: { Location: "/planning?planning=forbidden" } });
    const baseUrl = process.env.ONFLOWS_API_BASE_URL;
    const token = process.env.ONFLOWS_SERVICE_TOKEN;
    if (!baseUrl || !token) throw new Error("Server integration configuration is incomplete");

    stage = "form";
    const form = await request.formData();
    if (form.get("schema_version") !== "planning-calendar-v1")
      return redirect("calendar-invalid");
    const eventsJson = form.get("events_json");
    if (typeof eventsJson !== "string" || eventsJson.length > 100_000)
      return redirect("calendar-invalid");
    let submitted: unknown;
    try {
      submitted = JSON.parse(eventsJson);
    } catch {
      return redirect("calendar-invalid");
    }
    let calendar;
    try {
      calendar = parsePlanningCalendarInput({
        schema_version: "planning-calendar-v1",
        events: submitted,
      });
    } catch {
      return redirect("calendar-invalid");
    }

    stage = "api";
    await waitForApi(baseUrl);
    const response = await fetch(
      new URL("/api/v2/athlete/planning-calendar", baseUrl),
      {
        method: "PUT",
        cache: "no-store",
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-OnFlows-Athlete-Alias": athleteAlias,
        },
        body: JSON.stringify(calendar),
        signal: AbortSignal.timeout(75_000),
      },
    );
    stage = `api-response-${response.status}`;
    if (response.status === 409 || response.status === 422)
      return redirect("calendar-invalid");
    if (!response.ok) throw new Error("Planning calendar update failed");
    return redirect("calendar-saved");
  } catch {
    console.error(`Planning calendar update failed [${stage}]`);
    return redirect("calendar-error");
  }
}
