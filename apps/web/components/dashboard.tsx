import Link from "next/link";
import type { DataMode } from "../lib/api";
import type { CompletedWork } from "../lib/completed-work";
import type { LoadHistory } from "../lib/load-history";
import { TREF_BOUNDS_MINUTES, type TrainingStatus, type ZoneTrainingStatus } from "../lib/training-status";
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

const number = new Intl.NumberFormat("bg-BG", { maximumFractionDigits: 1 });
const decimal = (value: number) => number.format(value);
const date = (value: string) => new Intl.DateTimeFormat("bg-BG", { day: "2-digit", month: "long", year: "numeric", timeZone: "UTC" }).format(new Date(`${value}T00:00:00Z`));
const timestamp = (value: string) => new Intl.DateTimeFormat("bg-BG", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC", timeZoneName: "short" }).format(new Date(value));

const metrics: Array<[keyof ZoneTrainingStatus, string, (value: number) => string]> = [
  ["raw_time_min", "Реално време", (value) => `${decimal(value)} мин`],
  ["equivalent_time_min", "Еквивалентно време", (value) => `${decimal(value)} мин`],
  ["tref_min", "Tref · 40 дни", (value) => `${decimal(value)} мин`],
  ["status_7_40", "7/40", decimal],
  ["recovery_readiness_percent", "Готовност за натоварване", (value) => `${decimal(value)}%`],
  ["recovery_days_to_full", "Дни до пълно възстановяване", (value) => `${decimal(value)} дни`],
];

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
          <summary>Качество на данните{data.data_quality.warnings.length > 0 && <small className="quality-warning-count">{data.data_quality.warnings.length} бележки</small>} <span>История {decimal(data.data_quality.history_reliability * 100)}% · Последна активност {qualityScore === null ? "няма данни" : `${decimal(qualityScore * 100)}%`}</span><span aria-hidden="true">⌄</span></summary>
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

        {view === "details" && <section className="zones-section" aria-labelledby="zones-title">
          <div className="section-heading"><div><p className="section-kicker">Z1—Z5</p><h2 id="zones-title">Статус по зони</h2></div><p>Последна активност и текущ модел на възстановяване</p></div>
          {data.zones.length === 0 ? <div className="empty"><h3>Няма зонални данни</h3><p>API отговорът е валиден, но не съдържа зони за този анализ.</p></div> :
            <div className="zone-list">{data.zones.map((zone) => <ZoneCard key={zone.zone} zone={zone} recoveryV2={recoveryHistory?.schema_version === "recovery-history-v2"} />)}</div>}
        </section>}

        {view === "report" && <CompletedWorkSection report={completedWork} message={completedWorkMessage} selectable={mode === "api"} availablePeriodStart={loadHistory?.period_start} availablePeriodEnd={loadHistory?.period_end} />}
        {view === "load" && <><LoadHistorySection history={loadHistory} message={loadHistoryMessage} /><VolumeHistorySection history={volumeHistory} message={volumeHistoryMessage} /></>}
        {view === "recovery" && <RecoveryHistorySection history={recoveryHistory} message={recoveryHistoryMessage} refreshAvailable={integrationActions} syncBusy={syncBusy} fullRefreshRequired={loadHistory?.schema_version !== "load-history-v2"} canEdit={athleteCanEdit} />}

        {view === "details" && <details id="model-metadata" className="metadata">
          <summary><span><small>Техническа информация</small>Метаданни на модела</span><span className="chevron" aria-hidden="true">⌄</span></summary>
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

function ZoneCard({ zone, recoveryV2 = false }: { zone: ZoneTrainingStatus; recoveryV2?: boolean }) {
  const [trefMin, trefMax] = TREF_BOUNDS_MINUTES[zone.zone];
  return (
    <article className={`zone-card ${zone.zone.toLowerCase()}`} aria-labelledby={`title-${zone.zone}`}>
      <div className="zone-id"><span className="zone-mark" aria-hidden="true" /><div><p>Зона</p><h3 id={`title-${zone.zone}`}>{zone.zone}</h3></div></div>
      <dl>{metrics.map(([key, label, format]) => <div key={key}><dt>{key === "recovery_days_to_full" && recoveryV2 ? "Дни до 90% готовност" : label}</dt><dd>{format(zone[key] as number)}</dd>{key === "tref_min" && <small className="tref-bounds">7 × E40/ден · граници {trefMin}–{trefMax}</small>}</div>)}</dl>
    </article>
  );
}
