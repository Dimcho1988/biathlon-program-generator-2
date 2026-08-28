import { NextResponse } from "next/server";
import { currentAthleteAlias, multiProfileMode } from "../../../../lib/athlete-session";
import { waitForApi } from "../../../../lib/api-readiness";

const redirect = (state: "saved" | "invalid" | "error") =>
  new NextResponse(null, { status: 303, headers: { Location: `/planning?planning=${state}` } });
const calendarDate = (value: FormDataEntryValue | null) => {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const [year, month, day] = value.split("-").map(Number);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  return parsed.getUTCFullYear() === year && parsed.getUTCMonth() === month - 1 && parsed.getUTCDate() === day
    ? value
    : null;
};
const integer = (form: FormData, name: string, minimum: number, maximum: number) => {
  const raw = form.get(name);
  if (typeof raw !== "string" || !/^\d+$/.test(raw)) return null;
  const value = Number(raw);
  return Number.isInteger(value) && value >= minimum && value <= maximum ? value : null;
};
const weekdays = (form: FormData, name: string) => {
  const raw = form.getAll(name);
  const values = raw.map((value) => typeof value === "string" && /^[0-6]$/.test(value) ? Number(value) : -1);
  return values.every((value) => value >= 0) && new Set(values).size === values.length ? values : null;
};

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
    const schemaVersion = form.get("schema_version");
    const seasonStart = calendarDate(form.get("season_start"));
    const seasonEnd = calendarDate(form.get("season_end"));
    const annualTargetRaw = form.get("annual_target_hours");
    const annualTarget = typeof annualTargetRaw === "string" && /^\d+(?:\.\d+)?$/.test(annualTargetRaw)
      ? Number(annualTargetRaw)
      : null;
    const sessions = integer(form, "sessions_per_week", 1, 14);
    const restDays = weekdays(form, "rest_days");
    const doubleSessionDays = weekdays(form, "double_session_days");
    const longSessionDay = integer(form, "long_session_day", 0, 6);
    const intensityDays = weekdays(form, "intensity_days");
    const strengthDays = weekdays(form, "strength_days");
    const maxKeySessions = integer(form, "max_key_sessions_per_week", 0, 8);
    const mesocycleAnchor = calendarDate(form.get("mesocycle_anchor_date"));
    const mesocycleLength = integer(form, "mesocycle_length_weeks", 2, 6);
    const campAccentLimit = integer(form, "camp_default_accent_limit", 1, 6);
    const doubleThresholdRaw = form.get("double_threshold_enabled");
    const doubleThresholdEnabled = doubleThresholdRaw === null ? false : doubleThresholdRaw === "true";
    const doubleThresholdDay = integer(form, "double_threshold_day", 0, 6);
    const doubleThresholdComponents = form.getAll("double_threshold_components");
    const validComponents = doubleThresholdComponents.length > 0
      && new Set(doubleThresholdComponents).size === doubleThresholdComponents.length
      && doubleThresholdComponents.every((value) => value === "Z3" || value === "Z4");
    if (
      schemaVersion !== "planning-profile-v1"
      || !seasonStart
      || !seasonEnd
      || seasonEnd <= seasonStart
      || annualTarget === null
      || annualTarget < 50
      || annualTarget > 1500
      || sessions === null
      || restDays === null
      || restDays.length >= 7
      || doubleSessionDays === null
      || longSessionDay === null
      || intensityDays === null
      || strengthDays === null
      || maxKeySessions === null
      || !mesocycleAnchor
      || mesocycleLength === null
      || campAccentLimit === null
      || (doubleThresholdRaw !== null && !doubleThresholdEnabled)
      || doubleThresholdDay === null
      || !validComponents
      || doubleSessionDays.some((day) => restDays.includes(day))
      || sessions > (7 - restDays.length) + doubleSessionDays.length
      || (doubleThresholdEnabled && restDays.includes(doubleThresholdDay))
      || (doubleThresholdEnabled && !doubleSessionDays.includes(doubleThresholdDay))
      || (doubleThresholdEnabled && maxKeySessions < 2)
    ) return redirect("invalid");

    stage = "api";
    await waitForApi(baseUrl);
    const response = await fetch(new URL("/api/v2/athlete/planning-profile", baseUrl), {
      method: "PUT",
      cache: "no-store",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-OnFlows-Athlete-Alias": athleteAlias,
      },
      body: JSON.stringify({
        schema_version: schemaVersion,
        season_start: seasonStart,
        season_end: seasonEnd,
        annual_target_hours: annualTarget,
        sessions_per_week: sessions,
        rest_days: restDays,
        double_session_days: doubleSessionDays,
        long_session_day: longSessionDay,
        intensity_days: intensityDays,
        strength_days: strengthDays,
        max_key_sessions_per_week: maxKeySessions,
        mesocycle_anchor_date: mesocycleAnchor,
        mesocycle_length_weeks: mesocycleLength,
        camp_default_accent_limit: campAccentLimit,
        double_threshold_enabled: doubleThresholdEnabled,
        double_threshold_day: doubleThresholdDay,
        double_threshold_components: doubleThresholdComponents,
      }),
      signal: AbortSignal.timeout(75_000),
    });
    stage = `api-response-${response.status}`;
    if (response.status === 422) return redirect("invalid");
    if (!response.ok) throw new Error("Planning profile update failed");
    return redirect("saved");
  } catch {
    console.error(`Planning profile update failed [${stage}]`);
    return redirect("error");
  }
}
