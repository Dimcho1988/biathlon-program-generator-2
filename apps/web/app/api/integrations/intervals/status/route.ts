import { NextResponse } from "next/server";
import { currentAthleteAlias, multiProfileMode } from "../../../../../lib/athlete-session";
import { getSyncState } from "../../../../../lib/api";

const noStoreHeaders = { "Cache-Control": "no-store, max-age=0" };

export async function GET() {
  const athleteAlias = await currentAthleteAlias();
  if (multiProfileMode() && !athleteAlias)
    return NextResponse.json(
      { error: "athlete_session_required" },
      { status: 401, headers: noStoreHeaders },
    );
  try {
    const state = await getSyncState(athleteAlias ?? undefined, { direct: true });
    return NextResponse.json(state, { headers: noStoreHeaders });
  } catch (error) {
    console.error(`intervals_sync_status_failed error_type=${error instanceof Error ? error.name : "Unknown"}`);
    return NextResponse.json(
      { error: "sync_status_unavailable" },
      { status: 503, headers: noStoreHeaders },
    );
  }
}
