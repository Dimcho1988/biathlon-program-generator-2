import { NextResponse } from "next/server";
import { currentAthleteAlias, multiProfileMode } from "../../../../../lib/athlete-session";
import { parseSyncEnqueueResponse, type SyncScope } from "../../../../../lib/sync";

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

async function requestedRefreshOptions(request: Request): Promise<{
  returnTo: string;
  scope: SyncScope;
} | null> {
  try {
    const form = await request.formData();
    const requestedScope = form.get("scope");
    let scope: SyncScope;
    if (requestedScope === null) scope = "FULL";
    else if (requestedScope === "full" || requestedScope === "FULL") scope = "FULL";
    else if (requestedScope === "wellness" || requestedScope === "WELLNESS") scope = "WELLNESS";
    else if (requestedScope === "recovery" || requestedScope === "RECOVERY") scope = "RECOVERY";
    else return null;
    return {
      returnTo: safeReturnTo(form.get("returnTo")),
      scope,
    };
  }
  catch { return null; }
}

function withSyncState(returnTo: string, state: "queued" | "coalesced" | "enqueue-error") {
  const destination = new URL(returnTo, "https://onflows.invalid");
  destination.searchParams.set("sync", state);
  if (destination.pathname === "/") destination.searchParams.set("wake", "ready");
  return `${destination.pathname}${destination.search}`;
}

export async function POST(request: Request) {
  const options = await requestedRefreshOptions(request);
  if (!options)
    return NextResponse.json({ error: "invalid_sync_scope" }, { status: 400 });
  const { returnTo, scope } = options;
  try {
    const athleteAlias = await currentAthleteAlias();
    if (multiProfileMode() && !athleteAlias)
      return new NextResponse(null, { status: 303, headers: { Location: "/?intervals=session-required" } });
    const baseUrl = process.env.ONFLOWS_API_BASE_URL;
    const token = process.env.ONFLOWS_SERVICE_TOKEN;
    if (!baseUrl || !token) throw new Error("Server integration configuration is incomplete");
    const response = await fetch(new URL("/api/v2/real/sync-jobs", baseUrl), {
      method: "POST",
      cache: "no-store",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/json",
        "Content-Type": "application/json",
        ...(athleteAlias ? { "X-OnFlows-Athlete-Alias": athleteAlias } : {}),
      },
      body: JSON.stringify({ scope }),
      signal: AbortSignal.timeout(15_000),
    });
    if (response.status !== 202) {
      console.error(`intervals_sync_enqueue_failed stage=api status=${response.status}`);
      throw new Error("Sync enqueue failed");
    }
    const result = parseSyncEnqueueResponse(await response.json());
    console.info(`intervals_sync_enqueued state=${result.state} coalesced=${result.coalesced}`);
    return new NextResponse(null, {
      status: 303,
      headers: { Location: withSyncState(returnTo, result.coalesced ? "coalesced" : "queued") },
    });
  } catch (error) {
    if (!(error instanceof Error && error.message === "Sync enqueue failed"))
      console.error(`intervals_sync_enqueue_failed stage=web error_type=${error instanceof Error ? error.name : "Unknown"}`);
    return new NextResponse(null, { status: 303, headers: { Location: withSyncState(returnTo, "enqueue-error") } });
  }
}
