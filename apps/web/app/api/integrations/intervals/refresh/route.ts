import { NextResponse } from "next/server";
import { currentAthleteAlias, multiProfileMode } from "../../../../../lib/athlete-session";
import { getRecoveryHistory } from "../../../../../lib/api";
import { waitForApi } from "../../../../../lib/api-readiness";

function safeReturnTo(value: FormDataEntryValue | null) {
  if (typeof value !== "string" || !value.startsWith("/activities")) return "/";
  try {
    const parsed = new URL(value, "https://onflows.invalid");
    if (parsed.origin !== "https://onflows.invalid" || parsed.pathname !== "/activities") return "/";
    const start = parsed.searchParams.get("start");
    const end = parsed.searchParams.get("end");
    if (start && !/^\d{4}-\d{2}-\d{2}$/.test(start)) return "/";
    if (end && !/^\d{4}-\d{2}-\d{2}$/.test(end)) return "/";
    return `${parsed.pathname}${parsed.search}`;
  } catch {
    return "/";
  }
}

async function requestedRefreshOptions(request?: Request) {
  if (!request || request.method !== "POST") return { returnTo: "/", wellnessOnly: false, recoveryRestore: false };
  try {
    const form = await request.formData();
    const scope = form.get("scope");
    return {
      returnTo: safeReturnTo(form.get("returnTo")),
      wellnessOnly: scope === "wellness",
      recoveryRestore: scope === "recovery",
    };
  }
  catch { return { returnTo: "/", wellnessOnly: false, recoveryRestore: false }; }
}

function withRefreshState(returnTo: string, state: "refreshed" | "recovery-restored" | "refresh-error") {
  const destination = new URL(returnTo, "https://onflows.invalid");
  destination.searchParams.set("intervals", state);
  return `${destination.pathname}${destination.search}`;
}

async function refreshIntervalsData(request?: Request) {
  const { returnTo, wellnessOnly, recoveryRestore } = await requestedRefreshOptions(request);
  try {
    const athleteAlias = await currentAthleteAlias();
    if (multiProfileMode() && !athleteAlias)
      return new NextResponse(null, { status: 303, headers: { Location: "/?intervals=session-required" } });
    const baseUrl = process.env.ONFLOWS_API_BASE_URL;
    const token = process.env.ONFLOWS_SERVICE_TOKEN;
    if (!baseUrl || !token) throw new Error("Server integration configuration is incomplete");
    await waitForApi(baseUrl);
    const resource = wellnessOnly ? "/api/v2/real/wellness/refresh" : "/api/v2/real/refresh";
    const response = await fetch(new URL(resource, baseUrl), {
      method: "POST",
      cache: "no-store",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/json",
        ...(athleteAlias ? { "X-OnFlows-Athlete-Alias": athleteAlias } : {}),
      },
      signal: AbortSignal.timeout(wellnessOnly ? 75_000 : 180_000),
    });
    if (!response.ok) {
      console.error(`intervals_refresh_failed stage=api status=${response.status}`);
      throw new Error("Refresh failed");
    }
    const result = await response.json();
    if (
      !result || result.status !== "refreshed"
      || !Number.isInteger(result.wellness_records_received)
      || !Number.isInteger(result.wellness_days_stored)
      || (recoveryRestore && result.recovery_history_stored !== true)
    ) throw new Error("Refresh result was not persisted");
    // Prove the full API -> web read path before claiming recovery success.
    // This catches profile routing, persistence and frontend-contract failures,
    // rather than validating only the write inside the API process.
    if (recoveryRestore && !(await getRecoveryHistory(athleteAlias ?? undefined)))
      throw new Error("Recovery history could not be read back");
    console.info("intervals_refresh_completed");
    return new NextResponse(null, { status: 303, headers: { Location: withRefreshState(returnTo, recoveryRestore ? "recovery-restored" : "refreshed") } });
  } catch (error) {
    if (!(error instanceof Error && error.message === "Refresh failed"))
      console.error(`intervals_refresh_failed stage=web error_type=${error instanceof Error ? error.name : "Unknown"}`);
    return new NextResponse(null, { status: 303, headers: { Location: withRefreshState(returnTo, "refresh-error") } });
  }
}

export const POST = refreshIntervalsData;

// Some browsers can replay a form target as a navigation after a deployment or
// history restore. Refresh is an authenticated, read-only Intervals import, so
// route the fallback navigation through the same session-scoped handler instead of
// exposing a raw 405 page.
export const GET = refreshIntervalsData;
