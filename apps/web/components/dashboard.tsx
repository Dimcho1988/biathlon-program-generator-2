import Link from "next/link";
import type { DataMode } from "../lib/api";
import type { CompletedWork } from "../lib/completed-work";
import type { LoadHistory } from "../lib/load-history";
import { type TrainingStatus, type ZoneTrainingStatus } from "../lib/training-status";
import { LoadHistorySection } from "./load-history-section";
import { CompletedWorkSection } from "./completed-work-section";
import type { RecoveryHistory } from "../lib/recovery-history";
import { RecoveryHistorySection } from "./recovery-history-section";
import type { VolumeHistory } from "../lib/volume-history";
import { VolumeHistorySection } from "./volume-history-section";
import type { SyncState } from "../lib/sync";
import { syncInProgress } from "../lib/sync";
import { SyncStatusPanel } from "./sync-status-panel";
import { SyncActionForm } from "./sync-action-form";
import { roleLabel, type AccountRole } from "../lib/account-access";
import { DASHBOARD_VIEWS, dashboardHref, type DashboardViewKey } from "../lib/dashboard-navigation";
import { StatusOverview } from "./status-overview";
import { equivalentWindow, latestTrainingDay, displayDate } from "../lib/dashboard-periods";
import { VolumePeriodNote } from "./volume-period-note";
import { TrefDetails } from "./tref-details";

const number = new Intl.NumberFormat("bg-BG", { maximumFractionDigits: 1 });
const decimal = (value: number) => number.format(value);
const date = (value: string) => new Intl.DateTimeFormat("bg-BG", { day: "2-digit", month: "long", year: "numeric", timeZone: "UTC" }).format(new Date(`${value}T00:00:00Z`));
const timestamp = (value: string) => new Intl.DateTimeFormat("bg-BG", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC", timeZoneName: "short" }).format(new Date(value));


export function Dashboard({
  view = "overview",
  reportStart,
  reportEnd,
  data,
  mode,
  completedWork = null,
  loadHistory = null,
  recoveryHistory = null,
  volumeHistory = null,
  completedWorkMessage,
  loadHistoryMessage,
  recoveryHistoryMessage,
  volumeHistoryMessage,
  generationId,
  generationRevision,
  generationActivatedAt,
  syncState = null,
  integrationActions = false,
  sessionActions = false,
  athleteCanEdit = false,
  accountDisplayName,
  accountRoles = [],
  athleteDisplayName,
  notice,
}: {
  view?: DashboardViewKey;
  reportStart?: string;
  reportEnd?: string;
  data: TrainingStatus;
  mode: DataMode;
  completedWork?: CompletedWork | null;
  loadHistory?: LoadHistory | null;
  recoveryHistory?: RecoveryHistory | null;
  volumeHistory?: VolumeHistory | null;
  completedWorkMessage?: string;
  loadHistoryMessage?: string;
  recoveryHistoryMessage?: string;
  volumeHistoryMessage?: string;
  generationId?: string | null;
  generationRevision?: number;
  generationActivatedAt?: string | null;
  syncState?: SyncState | null;
  integrationActions?: boolean;
  sessionActions?: boolean;
  athleteCanEdit?: boolean;
  accountDisplayName?: string | null;
  accountRoles?: AccountRole[];
  athleteDisplayName?: string | null;
  notice?: string;
}) {
  const latest = loadHistory ? latestTrainingDay(loadHistory) : null;
  const short = loadHistory ? equivalentWindow(loadHistory, 7) : null;
  const long = loadHistory ? equivalentWindow(loadHistory, 40) : null;
  const recoveryV2 = recoveryHistory?.schema_version === "recovery-history-v2" ? recoveryHistory : null;
  const recoveryDate = recoveryV2?.as_of ?? recoveryHistory?.period_end ?? data.as_of;
  const qualityScore = data.data_quality.latest_activity_quality_score;
  const syncBusy = Boolean(syncState && syncInProgress(syncState));
  return (
    <main className="dashboard-page">
      <header className="page-heading" id="top">
        <div><p className="eyebrow">Тренировъчен анализ</p><h1>Общ статус</h1><p>{athleteDisplayName ?? "Тренировъчен статус"} · Данни до <time dateTime={data.as_of}>{date(data.as_of)}</time>{mode === "fixture" && <span className="demo-badge">Демо данни</span>}</p></div>
        {integrationActions && <SyncActionForm busy={syncBusy} returnTo={dashboardHref(view, reportStart, reportEnd)} />}
      </header>
      <nav className="dashboard-tabs" aria-label="Изглед на общия статус">{DASHBOARD_VIEWS.map(([key, label]) => <Link key={key} href={dashboardHref(key, reportStart, reportEnd)} prefetch={false} aria-current={view === key ? "page" : undefined}>{label}</Link>)}</nav>
      <section className="dashboard-content" aria-label="Тренировъчен анализ">
        {syncState && <SyncStatusPanel key={`${syncState.job_id ?? "idle"}:${syncState.state}:${generationId ?? "none"}`} initialState={syncState} renderedGenerationId={generationId ?? null} returnTo={dashboardHref(view, reportStart, reportEnd)} />}
        {notice && <p className="connection-notice">{notice}</p>}
        {view === "overview" && <StatusOverview data={data} recovery={recoveryHistory} load={loadHistory} />}
        {(view === "overview" || view === "details") && <details className="overview-quality">
          <summary>Качество на данните{data.data_quality.warnings.length > 0 && <small className="quality-warning-count">{data.data_quality.warnings.length} {data.data_quality.warnings.length === 1 ? "бележка" : "бележки"}</small>} <span>История {decimal(data.data_quality.history_reliability * 100)}% · Последна активност {qualityScore === null ? "няма данни" : `${decimal(qualityScore * 100)}%`}</span><span aria-hidden="true">⌄</span></summary>
          <section className="quality-panel" aria-labelledby="quality-title">
          <div><p className="section-kicker">Надеждност</p><h2 id="quality-title">Качество на данните</h2></div>
          <dl className="quality-values">
            <div><dt>История</dt><dd>{decimal(data.data_quality.history_reliability * 100)}%</dd></div>
            <div><dt>Последна активност</dt><dd>{qualityScore === null ? "Няма данни" : `${decimal(qualityScore * 100)}%`}</dd></div>
          </dl>
          {data.data_quality.warnings.length > 0 ? (
            <ul className="warnings" aria-label="Предупреждения">{data.data_quality.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
          ) : <p className="quality-ok"><span aria-hidden="true">✓</span> Няма предупреждения за качеството</p>}
          </section>
        </details>}

        {view === "details" && <>
          <section className="history-section" aria-labelledby="latest-day-title">
            <div className="section-heading"><div><p className="section-kicker">Извършено натоварване</p><h2 id="latest-day-title">Последен тренировъчен ден</h2></div><p>{latest ? `${displayDate(latest.day)} · ${latest.activities.length} ${latest.activities.length === 1 ? "активност" : "активности"}` : "Няма налична дата на тренировката"}</p></div>
            <p className="muted-copy">Всички обработени активности за посочената дата. Приравнените минути са към горната пулсова граница на зоната, без влияние от другите зони.</p>
            {latest ? <>
              <div className="activity-table-wrap"><table><thead><tr><th>Зона</th><th>Реално време</th><th>Приравнено време</th></tr></thead><tbody>{latest.zones.map((z) => <tr key={z.zone}><th>{z.zone}</th><td>{decimal(z.raw)} мин</td><td>{decimal(z.equivalent)} мин</td></tr>)}</tbody></table></div>
              {latest.strength > 0 && <p>Силова тренировка STR: {decimal(latest.strength)} мин; отделно от Z1–Z5.</p>}
              {latest.limited && <p className="muted-copy">Има активност с ограничено пулсово покритие; минутите по зони може да са непълни.</p>}
            </> : <p className="history-unavailable">Няма история за показване на тренировъчния ден. Не приписваме недатирани стойности на днешната дата.</p>}
          </section>
          <section className="zones-section" aria-labelledby="zones-title">
            <div className="section-heading"><div><p className="section-kicker">Z1—Z5</p><h2 id="zones-title">Текущо състояние по зони</h2></div><p>Готовност към {displayDate(recoveryDate)} · натоварване до {displayDate(loadHistory?.period_end ?? data.as_of)}</p></div>
            <p className="muted-copy">Готовността отчита остатъчната умора от предходните тренировки. Срокът е прогноза без ново натоварване, с дневна точност; 0 дни означава достигнат праг, а не липса на умора.</p>
            {recoveryV2?.source_stale && <p className="history-unavailable">Тренировъчната история изостава от датата на прогнозата. Приема се, че след последните данни няма ново натоварване.</p>}
            {loadHistory ? <VolumePeriodNote history={loadHistory} /> : <p className="muted-copy">Историята за обема не е налична.</p>}
            {data.zones.length === 0 ? <div className="empty"><h3>Няма зонални данни</h3><p>Няма зони за този анализ.</p></div> :
              <div className="zone-list">{data.zones.map((zone) => <ZoneCard key={zone.zone} zone={zone} recoveryV2={Boolean(recoveryV2)} volume7={short?.totals?.[zone.zone]} volume40={long?.weekly?.[zone.zone]} />)}</div>}
            <p className="muted-copy">7/40 сравнява ефективния товар E със стабилизираща база. Приравненият обем е отделен показател.</p>
          </section>
        </>}

        {view === "report" && <CompletedWorkSection report={completedWork} message={completedWorkMessage} selectable={mode === "api"} availablePeriodStart={loadHistory?.period_start} availablePeriodEnd={loadHistory?.period_end} />}
        {view === "load" && <><LoadHistorySection history={loadHistory} message={loadHistoryMessage} /><VolumeHistorySection history={volumeHistory} message={volumeHistoryMessage} /></>}
        {view === "recovery" && <RecoveryHistorySection history={recoveryHistory} message={recoveryHistoryMessage} refreshAvailable={integrationActions} syncBusy={syncBusy} fullRefreshRequired={loadHistory?.schema_version !== "load-history-v2"} canEdit={athleteCanEdit} />}

        {view === "details" && <details id="model-metadata" className="metadata">
          <summary><span><small>Техническа информация</small>Метаданни на модела</span><span className="chevron" aria-hidden="true">⌄</span></summary>
          <TrefDetails zones={data.zones} strength={loadHistory?.strength?.summary.tref_min} />
          <dl>
            {accountDisplayName && <div><dt>Акаунт</dt><dd>{accountDisplayName}</dd></div>}
            {accountRoles.length > 0 && <div><dt>Работни роли</dt><dd>{accountRoles.map(roleLabel).join(" · ")}</dd></div>}
            <div><dt>Избран спортист</dt><dd>{athleteDisplayName ?? data.athlete_id}</dd></div>
            <div><dt>Технически профил</dt><dd>{data.athlete_id}</dd></div>
            {generationRevision !== undefined && generationRevision > 0 && <div><dt>Активна версия</dt><dd>№ {generationRevision}</dd></div>}
            <div><dt>Версия на договора</dt><dd>{data.schema_version}</dd></div>
            <div><dt>Алгоритъм</dt><dd>{data.model.algorithm_version}</dd></div>
            <div><dt>Effective HR версия</dt><dd>{data.model.effective_hr_version}</dd></div>
            <div><dt>Effective HR източник</dt><dd>{data.model.effective_hr_source}</dd></div>
            <div><dt>Версия на параметрите</dt><dd>{data.model.parameter_version}</dd></div>
            {generationId && <div><dt>Generation ID</dt><dd>{generationId}</dd></div>}
            {generationActivatedAt && <div><dt>Активирана</dt><dd><time dateTime={generationActivatedAt}>{timestamp(generationActivatedAt)}</time></dd></div>}
          </dl>
        </details>}
      </section>
      {view === "details" && sessionActions && athleteCanEdit && <p className="dashboard-settings-link"><Link href="/?settings=edit">Зони и HRmax →</Link></p>}
    </main>
  );
}

function ZoneCard({ zone, recoveryV2 = false, volume7, volume40 }: { zone: ZoneTrainingStatus; recoveryV2?: boolean; volume7?: number; volume40?: number }) {
  const volume = (v: number | undefined) => v === undefined ? "Няма данни" : `${decimal(v)} мин`;
  return <article className={`zone-card current-zone-card ${zone.zone.toLowerCase()}`} aria-labelledby={`title-${zone.zone}`}>
    <div className="zone-id"><span className="zone-mark" aria-hidden="true" /><div><p>Зона</p><h3 id={`title-${zone.zone}`}>{zone.zone}</h3></div></div>
    <dl>
      <div><dt>Приравнено · 7 дни</dt><dd>{volume(volume7)}</dd></div>
      <div><dt>Приравнено · седмица от 40 дни</dt><dd>{volume(volume40)}</dd></div>
      <div><dt>7/40 · ефективен товар</dt><dd>{decimal(zone.status_7_40)}</dd></div>
      <div><dt>Готовност за натоварване</dt><dd>{decimal(zone.recovery_readiness_percent)}%</dd></div>
      <div><dt>{recoveryV2 ? "Дни до 90% готовност" : "Дни до пълно възстановяване"}</dt><dd>{decimal(zone.recovery_days_to_full)} дни</dd></div>
    </dl>
  </article>;
}
