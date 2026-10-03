import { parseActivePlanResponse, type ActivePlanResponse } from "./training-management";
import { isRecord } from "./training-status";

export const PLAN_CHANGED_NOTICE = "Програмата е обновена междувременно. Заредена е последната версия; прегледай я преди следващо действие.";

export function newerActivePlan(current: ActivePlanResponse, next: ActivePlanResponse): ActivePlanResponse {
  return current.active && (!next.active || next.active.revision < current.active.revision) ? current : next;
}

// A conflict reloads the current proposal. Never replay approval/day actions
// against a revision the coach has not reviewed.
export async function changeActivePlan(endpoint: string, body: Record<string, unknown>) {
  const response = await fetch(`/api/athlete/management/${endpoint}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (response.status === 409) {
    const latest = await fetch("/api/athlete/management/active", { cache: "no-store", signal: AbortSignal.timeout(75_000) });
    if (!latest.ok) throw new Error("Програмата е променена, но последната версия не се зареди. Презареди страницата преди следващо действие.");
    return { value: parseActivePlanResponse(await latest.json()), conflict: true };
  }
  const result: unknown = await response.json();
  if (!response.ok) throw new Error(isRecord(result) && typeof result.error === "string" ? result.error : "Програмата не беше променена.");
  return { value: parseActivePlanResponse(result), conflict: false };
}
