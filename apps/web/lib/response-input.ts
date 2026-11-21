import { FIELD_LABELS, type ResponseDay, type Session } from "./response-monitoring";
import { optionalNumber } from "./body-observations";
import { samplesFromForm, nmsFromForm } from "./training-observations";

/** One validated write contract for the form and future confirmed chat answers. */
export function dailyInput(form: FormData, day: ResponseDay, today: string) {
  return { day: day.day, observed_at: day.daily_report?.observed_at ?? (day.day === today ? new Date().toISOString() : null),
    ...Object.fromEntries(Object.keys(FIELD_LABELS).map(key => [key, optionalNumber(form, key)])),
    sleep_hours: optionalNumber(form, "sleep_hours"), pain_or_illness: form.get("pain_or_illness") === "on",
    note: String(form.get("note") || ""), input_method: "FORM", expected_revision: day.daily_revision };
}

export function sessionInput(form: FormData, session: Session, withMethods: boolean) {
  const rpe = optionalNumber(form, "rpe"), feel = optionalNumber(form, "feel");
  const source = (field: "rpe" | "feel", value: number | null) => value !== null && value === session[field]
    ? session[`${field}_source`] ?? (field === "rpe" ? session.source : null) ?? "ONFLOWS" : "ONFLOWS";
  return { activity_ref: session.activity_ref, rpe, feel, rpe_source: source("rpe", rpe), feel_source: source("feel", feel),
    duration_minutes: Number(form.get("duration_minutes")), timing: form.get("timing") || "UNKNOWN", input_method: "FORM",
    note: String(form.get("note") || ""), planned_duration_minutes: optionalNumber(form, "planned_duration_minutes"),
    planned_speed_kmh: optionalNumber(form, "planned_speed_kmh"), executed_speed_kmh: optionalNumber(form, "executed_speed_kmh"),
    execution_comparable: form.get("execution_comparable") === "on", execution_reason: form.get("execution_reason") || "UNKNOWN",
    ...(withMethods ? { executed_method_id: form.get("executed_method_id") || null, method_confirmed: form.get("method_confirmed") === "on" } : {}),
    lactate_samples: samplesFromForm(form), lactate_device: String(form.get("lactate_device") || ""), neuromuscular: nmsFromForm(form),
    expected_revision: session.revision };
}
