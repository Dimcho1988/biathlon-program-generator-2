import Image from "next/image";
import Link from "next/link";
import { ActivityDetailView } from "../../../components/activity-detail";
import { ErrorState } from "../../../components/error-state";
import { ThemeToggle } from "../../../components/theme-toggle";
import { currentAuthorizedAthlete } from "../../../lib/account-access";
import { multiProfileMode } from "../../../lib/athlete-session";
import { getActivityView } from "../../../lib/api";
import type { ActivityView } from "../../../lib/activities";

export default async function ActivityPage({ params }: { params: Promise<{ activityRef: string }> }) {
  const { activityRef } = await params;
  const athleteAlias = multiProfileMode() ? (await currentAuthorizedAthlete())?.athleteAlias : undefined;
  if (multiProfileMode() && !athleteAlias) return <ErrorState message="Няма активна защитена сесия за спортист." integrationActions refreshAvailable={false} />;
  let view: ActivityView;
  try {
    view = await getActivityView(activityRef, athleteAlias ?? undefined);
  } catch (error) {
    return <ErrorState message={error instanceof Error ? error.message : "Активността временно не е достъпна."} retryAvailable retryHref={`/activities/${encodeURIComponent(activityRef)}`} />;
  }
  return <main className="activity-detail-page"><nav className="detail-top-nav" aria-label="Основна навигация"><Link className="brand" href="/"><Image src="/brand/onflows-mark.png" width={33} height={40} alt="onFlows лого" /><span>onFlows</span></Link><div className="nav-actions"><Link href="/activities">Активности</Link>{multiProfileMode() && <Link href="/?settings=edit">Зони и HRmax</Link>}<ThemeToggle /></div></nav><ActivityDetailView activity={view.activity} series={view.series} /></main>;
}
