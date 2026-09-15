import Link from "next/link";
import { ActivityDetailView } from "../../../components/activity-detail";
import { ErrorState } from "../../../components/error-state";
import { currentAuthorizedAthlete } from "../../../lib/account-access";
import { multiProfileMode } from "../../../lib/athlete-session";
import { getActivityView } from "../../../lib/api";
import type { ActivityView } from "../../../lib/activities";

export default async function ActivityPage({ params }: { params: Promise<{ activityRef: string }> }) {
  const { activityRef } = await params;
  const access = multiProfileMode() ? await currentAuthorizedAthlete() : null;
  const athleteAlias = access?.athleteAlias;
  if (multiProfileMode() && !athleteAlias) return <ErrorState message="Няма активна защитена сесия за спортист." integrationActions refreshAvailable={false} />;
  let view: ActivityView;
  try {
    view = await getActivityView(activityRef, athleteAlias ?? undefined);
  } catch (error) {
    return <ErrorState message={error instanceof Error ? error.message : "Активността временно не е достъпна."} retryAvailable retryHref={`/activities/${encodeURIComponent(activityRef)}`} />;
  }
  return <main className="activity-detail-page"><nav className="detail-top-nav" aria-label="Път до активността"><Link href="/activities">← Към активностите</Link></nav><ActivityDetailView activity={view.activity} series={view.series} />{access?.canEditPlan&&access.canViewRecovery&&<p><Link className="action-button" href={`/speed?activity_ref=${activityRef}&sport=${encodeURIComponent(view.activity.sport)}`}>Използвай участък за скоростния модел</Link></p>}</main>;
}
