import { getSyncState } from "../../lib/api";
import type { SyncState } from "../../lib/sync";
import { currentAuthorizedAthlete } from "../../lib/account-access";
import { waitForApi } from "../../lib/api-readiness";
import { ErrorState } from "../../components/error-state";
import { TrainingManagement } from "../../components/training-management";
import { parseDrafts, parseManagementOutlook, type ManagementOutlook, parseManagementProfileResponse, parseActivePlanResponse, type ActivePlanResponse, type DraftRecord, type ManagementProfileResponse } from "../../lib/training-management";
import "./management.css";

export const dynamic = "force-dynamic";

export default async function ManagementPage({ searchParams }: { searchParams: Promise<{ sync?: string; view?: "week" | "overview" }> }) {
  const query = await searchParams;
  const access = await currentAuthorizedAthlete();
  if (!access) return <ErrorState message="Влезте в профила си, за да отворите управлението на подготовката." integrationActions refreshAvailable={false} />;
  if (!access.canViewPlan) return <ErrorState message="Този спортист не е споделил тренировъчния си план с вас." refreshAvailable={false} />;
  let sync: SyncState | null = null;
  let profile: ManagementProfileResponse;
  let drafts: DraftRecord[];
  let active: ActivePlanResponse;
  let outlook: ManagementOutlook | null = null;
  let inputFingerprint = "";
  try {
    const base = process.env.ONFLOWS_API_BASE_URL;
    const token = process.env.ONFLOWS_SERVICE_TOKEN;
    if (!base || !token) throw new Error("Управлението временно не е достъпно.");
    await waitForApi(base);
    const resource = async (path: string) => {
      const response = await fetch(new URL(`/api/v2/athlete/management/${path}`, base), {
        cache: "no-store", signal: AbortSignal.timeout(75_000),
        headers: { Authorization: `Bearer ${token}`, "X-OnFlows-Athlete-Alias": access.athleteAlias, "X-OnFlows-Actor-Id": access.actorUserId },
      });
      if (!response.ok) throw new Error("Профилът и програмите временно не са достъпни.");
      return response.json();
    };
    const [profileData, draftData, activeData, syncData, outlookData] = await Promise.all([resource("profile"), query.view === "overview" ? Promise.resolve({ drafts: [] }) : resource("drafts"), resource("active"), getSyncState(access.athleteAlias, { direct: true }).catch(() => null), query.view === "overview" ? resource("outlook") : Promise.resolve(null)]);
    sync = syncData;
    if (outlookData !== null) outlook = parseManagementOutlook(outlookData);
    active = parseActivePlanResponse(activeData);
    profile = parseManagementProfileResponse(profileData);
    drafts = parseDrafts(draftData);
    inputFingerprint = typeof draftData.current_input_fingerprint === "string" ? draftData.current_input_fingerprint : "";
  } catch (caught) {
    return <ErrorState message={caught instanceof Error ? caught.message : "Управлението временно не е достъпно."} retryAvailable retryHref={query.view === "overview" ? "/management/outlook" : "/management"} />;
  }
  return <TrainingManagement key={`${access.athleteAlias}:${sync?.active_generation_id ?? "none"}:${query.sync ?? ""}:${profile.revision}:${inputFingerprint}:${query.view ?? "week"}`} initialView={query.view ?? "week"} athleteName={access.displayName} canEdit={access.canEditPlan}
    initialOutlook={outlook} initialSyncState={sync} syncError={query.sync === "enqueue-error"} initialProfile={profile} initialDrafts={drafts} initialActive={active} today={profile.today ?? new Date().toISOString().slice(0, 10)} />;
}
