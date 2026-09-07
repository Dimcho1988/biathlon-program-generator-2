import Image from "next/image";
import Link from "next/link";
import { TrainabilityHistoryView } from "../../components/trainability-history";
import { ErrorState } from "../../components/error-state";
import { ThemeToggle } from "../../components/theme-toggle";
import { SyncActionForm } from "../../components/sync-action-form";
import { SyncStatusPanel } from "../../components/sync-status-panel";
import { SyncPendingState } from "../../components/sync-pending-state";
import { currentAuthorizedAthlete } from "../../lib/account-access";
import { multiProfileMode } from "../../lib/athlete-session";
import { getTrainabilityHistory, getSyncState } from "../../lib/api";
import { isCalendarDate } from "../../lib/training-status";
import { syncInProgress } from "../../lib/sync";

export default async function TrainabilityPage({ searchParams }: { searchParams: Promise<{ start?: string; end?: string }> }) {
  const query = await searchParams;
  const athleteAlias = multiProfileMode() ? (await currentAuthorizedAthlete())?.athleteAlias : undefined;
  if (multiProfileMode() && !athleteAlias) return <ErrorState message="Няма активна защитена сесия за спортист." integrationActions refreshAvailable={false} />;
  const end = isCalendarDate(query.end) ? query.end! : new Date().toISOString().slice(0, 10);
  const shifted = new Date(`${end}T12:00:00Z`); shifted.setUTCDate(shifted.getUTCDate() - 89);
  const start = isCalendarDate(query.start) ? query.start! : shifted.toISOString().slice(0, 10);
  if (start > end || Date.parse(end) - Date.parse(start) >= 90 * 86400000) return <main className="activities-page"><h1>Индекс на тренираност</h1><p>Изберете период от 1 до 90 дни.</p><Link href="/trainability">Покажи последните 90 дни</Link></main>;
  const [historyResult, syncResult] = await Promise.allSettled([
    getTrainabilityHistory(athleteAlias, start, end), getSyncState(athleteAlias),
  ]);
  const sync = syncResult.status === "fulfilled" ? syncResult.value : null;
  if (historyResult.status === "rejected") {
    if (sync && syncInProgress(sync) && sync.active_generation_id === null) return <SyncPendingState state={sync} />;
    return <ErrorState message={historyResult.reason instanceof Error ? historyResult.reason.message : "Индексът временно не е достъпен."} retryAvailable />;
  }
  const history = historyResult.value;
  const returnTo = `/trainability?start=${start}&end=${end}`;
  const fixture = process.env.ONFLOWS_DATA_MODE === "fixture";
  return <main className="activities-page trainability-page">
    <header className="activities-hero"><nav aria-label="Основна навигация"><Link className="brand" href="/"><Image src="/brand/onflows-mark.png" width={33} height={40} alt="onFlows лого" priority /><span>onFlows</span></Link><div className="nav-actions"><Link href="/">Тренировъчен статус</Link><Link href="/activities">Активности</Link><ThemeToggle /></div></nav>
      <div className="activities-title"><div><p className="eyebrow">Динамика по активности</p><h1>Индекс на тренираност</h1><p>Z1–Z5 и отделен общ индекс за 75–92% HRmax.</p></div><span>{fixture ? "Примерни данни" : `Версия на данните № ${history.revision}`}</span></div></header>
    <section className="index-period-controls" aria-label="Период на индекса"><form method="get"><label>От<input type="date" name="start" defaultValue={start} required /></label><label>До<input type="date" name="end" defaultValue={end} required /></label><button className="action-button secondary" type="submit">Покажи периода</button></form>{!fixture && <SyncActionForm returnTo={returnTo} busy={Boolean(sync && syncInProgress(sync))} />}</section>
    {sync && <SyncStatusPanel key={`${sync.job_id}:${sync.state}:${history.generation_id}`} initialState={sync} renderedGenerationId={history.generation_id} returnTo={returnTo} />}
    <TrainabilityHistoryView key={`${start}:${end}:${history.generation_id}`} history={history} />
  </main>;
}
