import Link from "next/link";
import { ShadowActivityPanel } from "../../../../components/shadow-activity-panel";
import { ErrorState } from "../../../../components/error-state";
import { currentAthleteAlias, multiProfileMode } from "../../../../lib/athlete-session";
import { getActivityView, getAthleteSettings } from "../../../../lib/api";
import type { ActivityView } from "../../../../lib/activities";

export default async function ActivityShadowPage({ params }: { params: Promise<{ activityRef: string }> }) {
  const { activityRef } = await params;
  const athleteAlias = multiProfileMode() ? await currentAthleteAlias() : undefined;
  if (multiProfileMode() && !athleteAlias) return <ErrorState message="Няма активна защитена сесия за спортист." integrationActions refreshAvailable={false} />;
  let view: ActivityView;
  let profileHrRange: readonly [number, number] | null = null;
  try {
    const [viewResult, settingsResult] = await Promise.allSettled([
      getActivityView(activityRef, athleteAlias ?? undefined),
      athleteAlias ? getAthleteSettings(athleteAlias) : Promise.resolve(null),
    ]);
    if (viewResult.status === "rejected") throw viewResult.reason;
    view = viewResult.value;
    if (settingsResult.status === "fulfilled" && settingsResult.value?.hr_zone_bounds_bpm && settingsResult.value.hrmax_bpm !== null) {
      profileHrRange = [settingsResult.value.hr_zone_bounds_bpm[0], settingsResult.value.hrmax_bpm];
    }
  } catch (error) { return <ErrorState message={error instanceof Error ? error.message : "Experimental резултатът не е достъпен."} retryAvailable />; }
  if (view.shadow === null) return <ErrorState message="За тази активност няма generation-pinned experimental резултат." retryAvailable />;
  const activity = view.activity;
  const payload = view.shadow;
  return <main className="shadow-page"><nav className="detail-top-nav" aria-label="Навигация на experimental анализа"><Link href={`/activities/${activityRef}`}>← Canonical активност</Link>{multiProfileMode() && <Link href="/?settings=edit">Зони и HRmax</Link>}</nav><header className="shadow-hero"><div><p className="eyebrow">Experimental / shadow</p><h1>{activity.name || `${activity.sport} · ${activity.local_time}`}</h1><p className="shadow-intro">Vflat B65 и HRmod v4 са диагностични канали. Не променят canonical load, recovery, 7/40, реалните HR зони или тренировъчния план.</p></div><span className="configuration-badge">Не влияе на основния модел</span></header><ShadowActivityPanel payload={payload} activityRef={activityRef} profileHrRange={profileHrRange} /></main>;
}
