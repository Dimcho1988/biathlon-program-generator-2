import { getManagementView } from "../../lib/management-server";
import type { SyncState } from "../../lib/sync";
import { currentAuthorizedAthlete } from "../../lib/account-access";
import { ErrorState } from "../../components/error-state";
import { TrainingManagement } from "../../components/training-management";
import { type ManagementOutlook, type ActivePlanResponse, type DraftRecord, type ManagementProfileResponse } from "../../lib/training-management";
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
    ({ profile, active, drafts, outlook, sync, inputFingerprint } = await getManagementView(
      access.athleteAlias, access.actorUserId, query.view ?? "week",
    ));
  } catch (caught) {
    return <ErrorState message={caught instanceof Error ? caught.message : "Управлението временно не е достъпно."} retryAvailable retryHref={query.view === "overview" ? "/management/outlook" : "/management"} />;
  }
  return <TrainingManagement key={`${access.athleteAlias}:${sync?.active_generation_id ?? "none"}:${query.sync ?? ""}:${profile.revision}:${inputFingerprint}:${query.view ?? "week"}`} initialView={query.view ?? "week"} athleteName={access.displayName} canEdit={access.canEditPlan}
    initialOutlook={outlook} initialSyncState={sync} syncError={query.sync === "enqueue-error"} initialProfile={profile} initialDrafts={drafts} initialActive={active} today={profile.today ?? new Date().toISOString().slice(0, 10)} />;
}
