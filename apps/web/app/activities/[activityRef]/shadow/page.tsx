import Link from "next/link";
import { ShadowActivityPanel } from "../../../../components/shadow-activity-panel";
import { ErrorState } from "../../../../components/error-state";
import { currentAthleteAlias, multiProfileMode } from "../../../../lib/athlete-session";
import { getActivityDetail, getActivityShadow } from "../../../../lib/api";
import type { ActivityDetail } from "../../../../lib/activities";

export default async function ActivityShadowPage({ params }: { params: Promise<{ activityRef: string }> }) {
  const { activityRef } = await params;
  const athleteAlias = multiProfileMode() ? await currentAthleteAlias() : undefined;
  if (multiProfileMode() && !athleteAlias) return <ErrorState message="Няма активна защитена сесия за спортист." integrationActions refreshAvailable={false} />;
  let activity: ActivityDetail;
  let payload: Record<string, unknown>;
  try {
    [activity, payload] = await Promise.all([getActivityDetail(activityRef, athleteAlias ?? undefined), getActivityShadow(athleteAlias ?? "", activityRef)]);
  } catch (error) { return <ErrorState message={error instanceof Error ? error.message : "Experimental резултатът не е достъпен."} retryAvailable />; }
  return <main className="shadow-page"><nav className="detail-top-nav" aria-label="Навигация на experimental анализа"><Link href={`/activities/${activityRef}`}>← Canonical активност</Link>{multiProfileMode() && <Link href="/?settings=edit">Зони и HRmax</Link>}</nav><header className="shadow-hero"><div><p className="eyebrow">Experimental / shadow</p><h1>{activity.name || `${activity.sport} · ${activity.local_time}`}</h1><p className="shadow-intro">Vflat B65 и HRmod v4 са диагностични канали. Не променят canonical load, recovery, 7/40, реалните HR зони или тренировъчния план.</p></div><span className="configuration-badge">Не влияе на основния модел</span></header><ShadowActivityPanel payload={payload} activityRef={activityRef} /></main>;
}
