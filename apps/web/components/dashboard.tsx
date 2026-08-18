import Image from "next/image";
import Link from "next/link";
import type { DataMode } from "../lib/api";
import type { CompletedWork } from "../lib/completed-work";
import type { LoadHistory } from "../lib/load-history";
import type { TrainingStatus, ZoneTrainingStatus } from "../lib/training-status";
import { LoadHistorySection } from "./load-history-section";
import { CompletedWorkSection } from "./completed-work-section";
import type { RecoveryHistory } from "../lib/recovery-history";
import { RecoveryHistorySection } from "./recovery-history-section";
import { ThemeToggle } from "./theme-toggle";
import type { VolumeHistory } from "../lib/volume-history";
import { VolumeHistorySection } from "./volume-history-section";

const number = new Intl.NumberFormat("bg-BG", { maximumFractionDigits: 1 });
const decimal = (value: number) => number.format(value);
const date = (value: string) => new Intl.DateTimeFormat("bg-BG", { day: "2-digit", month: "long", year: "numeric", timeZone: "UTC" }).format(new Date(`${value}T00:00:00Z`));

const metrics: Array<[keyof ZoneTrainingStatus, string, (value: number) => string]> = [
  ["raw_time_min", "Реално време", (value) => `${decimal(value)} мин`],
  ["equivalent_time_min", "Еквивалентно време", (value) => `${decimal(value)} мин`],
  ["tref_min", "Tref", (value) => `${decimal(value)} мин`],
  ["status_7_40", "7/40", decimal],
  ["recovery_readiness_percent", "Готовност за натоварване", (value) => `${decimal(value)}%`],
  ["recovery_days_to_full", "Дни до пълно възстановяване", (value) => `${decimal(value)} дни`],
];

const analysisSections = [
  ["#quality-title", "Качество"],
  ["#zones-title", "Статус по зони"],
  ["#completed-work-title", "Извършена работа"],
  ["#volume-title", "Общ обем"],
  ["#history-title", "7/40 и зонален товар"],
  ["#recovery-title", "Възстановяване"],
  ["#model-metadata", "Версии на моделите"],
] as const;

function AnalysisNavigation({ planningAvailable }: { planningAvailable: boolean }) {
  return <nav className="analysis-nav" aria-label="Модули на тренировъчния анализ">
    <p className="analysis-nav-label">Модули</p>
    {analysisSections.map(([href, label]) => <a key={href} href={href}>{label}</a>)}
    {planningAvailable && <Link href="/planning">Профил за планиране</Link>}
  </nav>;
}

export function Dashboard({
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
  integrationActions = false,
  sessionActions = false,
  notice,
}: {
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
  integrationActions?: boolean;
  sessionActions?: boolean;
  notice?: string;
}) {
  const qualityScore = data.data_quality.latest_activity_quality_score;
  return (
    <main>
      <header className="hero">
        <nav aria-label="Основна навигация">
          <a className="brand" href="#top" aria-label="onFlows начало"><Image src="/brand/onflows-mark.png" width={33} height={40} alt="onFlows лого" priority /><span>onFlows</span></a>
          <div className="nav-actions"><span className="product">Performance intelligence</span><ThemeToggle /></div>
        </nav>
        <div id="top" className="hero-grid">
          <div>
            <p className="eyebrow">Анализ на натоварването</p>
            <h1>Тренировъчен статус</h1>
            <p className="intro">Ясен зонален поглед върху текущото натоварване и възстановяване.</p>
          </div>
          <dl className="identity">
            <div><dt>Спортист</dt><dd>{data.athlete_id}</dd></div>
            <div><dt>Анализ към</dt><dd><time dateTime={data.as_of}>{date(data.as_of)}</time></dd></div>
            {mode === "fixture" && <div className="demo-badge" aria-label="Режим с демо данни"><span aria-hidden="true" />Демо данни</div>}
          </dl>
        </div>
      </header>

      <section className="content" aria-label="Тренировъчен анализ">
        <AnalysisNavigation planningAvailable={sessionActions} />
        <div className="analysis-main">
        {notice && <p className="connection-notice">{notice}</p>}
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

        <section className="zones-section" aria-labelledby="zones-title">
          <div className="section-heading"><div><p className="section-kicker">Z1—Z5</p><h2 id="zones-title">Статус по зони</h2></div><p>Последна активност и текущ модел на възстановяване</p></div>
          {data.zones.length === 0 ? <div className="empty"><h3>Няма зонални данни</h3><p>API отговорът е валиден, но не съдържа зони за този анализ.</p></div> :
            <div className="zone-list">{data.zones.map((zone) => <ZoneCard key={zone.zone} zone={zone} />)}</div>}
        </section>

        <CompletedWorkSection report={completedWork} message={completedWorkMessage} selectable={mode === "api"} availablePeriodStart={loadHistory?.period_start} availablePeriodEnd={loadHistory?.period_end} />
        <VolumeHistorySection history={volumeHistory} message={volumeHistoryMessage} />
        <LoadHistorySection history={loadHistory} message={loadHistoryMessage} />
        <RecoveryHistorySection history={recoveryHistory} message={recoveryHistoryMessage} />

        <details id="model-metadata" className="metadata">
          <summary><span><small>Техническа информация</small>Метаданни на модела</span><span className="chevron" aria-hidden="true">⌄</span></summary>
          <dl>
            <div><dt>Версия на договора</dt><dd>{data.schema_version}</dd></div>
            <div><dt>Алгоритъм</dt><dd>{data.model.algorithm_version}</dd></div>
            <div><dt>Effective HR версия</dt><dd>{data.model.effective_hr_version}</dd></div>
            <div><dt>Effective HR източник</dt><dd>{data.model.effective_hr_source}</dd></div>
            <div><dt>Версия на параметрите</dt><dd>{data.model.parameter_version}</dd></div>
          </dl>
        </details>
        </div>
      </section>
      <footer><span className="footer-brand">onFlows</span><p>Данните са диагностичен изглед на съществуващия модел.</p><div>{integrationActions && <form action="/api/integrations/intervals/refresh" method="post"><button className="text-action" type="submit">Обнови данните</button></form>}{sessionActions && <Link className="text-action" href="/?settings=edit">Настройки</Link>}{sessionActions && <form action="/api/session/logout" method="post"><button className="text-action" type="submit">Смени профила</button></form>}</div></footer>
    </main>
  );
}

function ZoneCard({ zone }: { zone: ZoneTrainingStatus }) {
  return (
    <article className={`zone-card ${zone.zone.toLowerCase()}`} aria-labelledby={`title-${zone.zone}`}>
      <div className="zone-id"><span className="zone-mark" aria-hidden="true" /><div><p>Зона</p><h3 id={`title-${zone.zone}`}>{zone.zone}</h3></div></div>
      <dl>{metrics.map(([key, label, format]) => <div key={key}><dt>{label}</dt><dd>{format(zone[key] as number)}</dd></div>)}</dl>
    </article>
  );
}
