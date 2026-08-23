import Image from "next/image";
import Link from "next/link";
import { ActivityDetailView } from "../../../components/activity-detail";
import { ErrorState } from "../../../components/error-state";
import { ThemeToggle } from "../../../components/theme-toggle";
import { currentAthleteAlias, multiProfileMode } from "../../../lib/athlete-session";
import { getActivityDetail, getActivitySeries } from "../../../lib/api";

export default async function ActivityPage({ params }: { params: Promise<{ activityRef: string }> }) {
  const { activityRef } = await params;
  const athleteAlias = multiProfileMode() ? await currentAthleteAlias() : undefined;
  if (multiProfileMode() && !athleteAlias) return <ErrorState message="Няма активна защитена сесия за спортист." integrationActions refreshAvailable={false} />;
  let activity, series;
  try { [activity, series] = await Promise.all([getActivityDetail(activityRef, athleteAlias ?? undefined), getActivitySeries(activityRef, athleteAlias ?? undefined)]); }
  catch (error) { return <ErrorState message={error instanceof Error ? error.message : "Активността временно не е достъпна."} retryAvailable />; }
  return <main className="activity-detail-page"><nav className="detail-top-nav" aria-label="Основна навигация"><Link className="brand" href="/"><Image src="/brand/onflows-mark.png" width={33} height={40} alt="onFlows лого" /><span>onFlows</span></Link><div className="nav-actions"><Link href="/activities">Активности</Link><ThemeToggle /></div></nav><ActivityDetailView activity={activity} series={series} /></main>;
}
