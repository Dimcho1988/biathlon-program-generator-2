import Image from "next/image";
import Link from "next/link";
import { ActivityCalendarView } from "../../components/activity-calendar";
import { ErrorState } from "../../components/error-state";
import { ThemeToggle } from "../../components/theme-toggle";
import { currentAuthorizedAthlete } from "../../lib/account-access";
import { multiProfileMode } from "../../lib/athlete-session";
import { getActivityCalendar, getSyncState } from "../../lib/api";
import { syncInProgress } from "../../lib/sync";
import { SyncStatusPanel } from "../../components/sync-status-panel";
import { SyncPendingState } from "../../components/sync-pending-state";

const iso = (value: Date) => value.toISOString().slice(0, 10);
const shifted = (value: string, days: number) => { const date = new Date(`${value}T00:00:00Z`); date.setUTCDate(date.getUTCDate() + days); return iso(date); };
const validDate = (value?: string) => value && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : undefined;

export default async function ActivitiesPage({ searchParams }: { searchParams: Promise<{ start?: string; end?: string; sync?: string }> }) {
  const query = await searchParams;
  const athleteAlias = multiProfileMode() ? (await currentAuthorizedAthlete())?.athleteAlias : undefined;
  if (multiProfileMode() && !athleteAlias) return <ErrorState message="Няма активна защитена сесия за спортист." integrationActions refreshAvailable={false} />;
  const today = new Date();
  const end = validDate(query.end) ?? iso(today);
  const start = validDate(query.start) ?? shifted(end, -41);
  const [calendarResult, syncResult] = await Promise.allSettled([
    getActivityCalendar(athleteAlias ?? undefined, start, end),
    getSyncState(athleteAlias ?? undefined),
  ]);
  const syncState = syncResult.status === "fulfilled" ? syncResult.value : null;
  if (calendarResult.status === "rejected") {
    if (syncState && syncInProgress(syncState) && syncState.active_generation_id === null)
      return <SyncPendingState state={syncState} />;
    return <ErrorState message={calendarResult.reason instanceof Error ? calendarResult.reason.message : "Календарът временно не е достъпен."} retryAvailable />;
  }
  const calendar = calendarResult.value;
  const syncBusy = Boolean(syncState && syncInProgress(syncState));
  const span = Math.max(1, Math.round((Date.parse(`${end}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`)) / 86400000) + 1);
  return <main className="activities-page">
    <header className="activities-hero">
      <nav aria-label="Основна навигация"><Link className="brand" href="/"><Image src="/brand/onflows-mark.png" width={33} height={40} alt="onFlows лого" priority /><span>onFlows</span></Link><div className="nav-actions"><Link href="/">Тренировъчен статус</Link>{multiProfileMode() && <Link href="/?settings=edit">Зони и HRmax</Link>}<ThemeToggle /></div></nav>
      <div className="activities-title"><div><p className="eyebrow">Реално завършена работа</p><h1>Активности</h1><p>Календар, canonical анализи и ясна връзка към experimental сравненията.</p></div><span>{calendar.activities.length} активности · {calendar.period_start} — {calendar.period_end}{calendar.revision ? ` · версия ${calendar.revision}` : ""}</span></div>
    </header>
    {syncState && <SyncStatusPanel key={`${syncState.job_id ?? "idle"}:${syncState.state}:${calendar.generation_id ?? "none"}`} initialState={syncState} renderedGenerationId={calendar.generation_id ?? null} returnTo={`/activities?start=${calendar.period_start}&end=${calendar.period_end}`} />}
    <section className="activity-period-controls" aria-label="Период на календара">
      <Link href={`/activities?start=${shifted(start, -span)}&end=${shifted(end, -span)}`}>← Предишен период</Link>
      <div><Link href={`/activities?start=${shifted(end, -29)}&end=${end}`}>30 дни</Link><Link href={`/activities?start=${shifted(end, -59)}&end=${end}`}>60 дни</Link><Link href={`/activities?start=${shifted(end, -89)}&end=${end}`}>90 дни</Link></div>
      <Link href={`/activities?start=${shifted(start, span)}&end=${shifted(end, span)}`}>Следващ период →</Link>
    </section>
    <ActivityCalendarView calendar={calendar} syncBusy={syncBusy} />
  </main>;
}
