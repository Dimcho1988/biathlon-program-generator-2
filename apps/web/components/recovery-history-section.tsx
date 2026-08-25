import type { CSSProperties } from "react";
import type { RecoveryHistory, WellnessCoverageDiagnostics, WellnessCoverageField } from "../lib/recovery-history";
import { ZONES, type Zone } from "../lib/training-status";

const number = new Intl.NumberFormat("bg-BG", { maximumFractionDigits: 1 });
const parameterNumber = new Intl.NumberFormat("bg-BG", { maximumFractionDigits: 2 });
const decimal = (value: number) => number.format(value);
const parameter = (value: number) => parameterNumber.format(value);
const date = (value: string) => new Intl.DateTimeFormat("bg-BG", { day: "2-digit", month: "short", timeZone: "UTC" }).format(new Date(`${value}T00:00:00Z`));
const zoneStyle = (zone: Zone): CSSProperties => ({ "--series": `var(--zone-${ZONES.indexOf(zone) + 1})` } as CSSProperties);
const strengthStyle = { "--series": "var(--strength)" } as CSSProperties;
const wellnessLabels: Record<WellnessCoverageField, string> = {
  sleep_duration: "Продължителност на съня",
  sleep_score: "Sleep score",
  sleep_quality: "Качество на съня",
  resting_hr: "Пулс в покой",
  average_sleeping_hr: "Среден пулс по време на сън",
  hrv: "HRV (rMSSD)",
  hrv_sdnn: "HRV (SDNN)",
  readiness: "Readiness / recovery",
  respiration: "Дихателна честота",
  spo2: "SpO₂",
  fatigue: "Умора",
  stress: "Стрес",
  mood: "Настроение",
  motivation: "Мотивация",
  soreness: "Обща болезненост",
  injury: "Injury",
};
const unresolvedLabels: Record<WellnessCoverageDiagnostics["unresolved_canonical_inputs"][number], string> = {
  soreness_legs: "болезненост на краката",
  soreness_upper: "болезненост на горната част",
  pain: "болка",
  illness: "заболяване/симптоми",
};
const freshnessLabels = { fresh: "актуални", stale: "остарели", unknown: "неизвестна актуалност" } as const;

function WellnessCoveragePanel({ diagnostics }: { diagnostics: WellnessCoverageDiagnostics }) {
  return <details className="wellness-diagnostics">
    <summary>
      <span><small>{diagnostics.schema_version} · не влияе върху recovery</small>Покритие на wellness данните</span>
      <span className="wellness-summary-value">{diagnostics.days_with_any_recognized_data}/{diagnostics.calendar_days} дни · {decimal(diagnostics.daily_presence_percent)}%</span>
      <span className="chevron" aria-hidden="true">⌄</span>
    </summary>
    <div className="wellness-diagnostics-body">
      <dl className="wellness-coverage-metrics">
        <div><dt>Получени записи</dt><dd>{diagnostics.records_received}</dd></div>
        <div><dt>Дни с разпознати данни</dt><dd>{diagnostics.days_with_any_recognized_data}/{diagnostics.calendar_days}</dd></div>
        <div><dt>Покритие по дни</dt><dd>{decimal(diagnostics.daily_presence_percent)}%</dd></div>
        <div><dt>Покритие поле × ден</dt><dd>{decimal(diagnostics.recognized_field_coverage_percent)}%</dd></div>
        <div><dt>Последен запис</dt><dd>{diagnostics.latest_observed_date ? date(diagnostics.latest_observed_date) : "Няма"}</dd></div>
        <div><dt>Актуалност</dt><dd>{freshnessLabels[diagnostics.freshness]}</dd></div>
      </dl>
      <p className="wellness-privacy-note">Запазени са само агрегирани бройки за покритие — не и wellness стойностите. Липсващите дни и полета не се заместват с неутрални стойности.</p>
      <div className="activity-table-wrap"><table><thead><tr><th>Показател</th><th>Поле в Intervals</th><th>Валидни дни</th><th>Покритие</th></tr></thead><tbody>{diagnostics.fields.map((field) => <tr key={field.field}><th>{wellnessLabels[field.field]}</th><td>{field.source_fields.join(" / ")}</td><td>{field.valid_days}/{diagnostics.calendar_days}{field.invalid_days > 0 ? ` · ${field.invalid_days} невалидни` : ""}</td><td>{decimal(field.coverage_percent)}%</td></tr>)}</tbody></table></div>
      <p className="wellness-unresolved"><strong>Все още нерешени canonical входове:</strong> {diagnostics.unresolved_canonical_inputs.map((input) => unresolvedLabels[input]).join(", ")}. Общото поле <code>soreness</code> не се разделя автоматично по части на тялото.</p>
    </div>
  </details>;
}

function RecoverySettingsHelp() {
  return <details className="recovery-settings-help">
    <summary><span className="help-icon" aria-hidden="true">?</span><strong>Какво означават показателите?</strong></summary>
    <div className="recovery-settings-help-body">
      <p>Това са параметри на текущата версионирана конфигурация, а не директни измервания на спортиста. Засега са само за преглед и не се персонализират автоматично.</p>
      <p className="recovery-formula"><code>Δумора = 100 × чувствителност × E / Tref</code>, където <code>E</code> е ефективният товар в съответната зона.</p>
      <dl className="recovery-parameter-help">
        <div><dt>Tref · референтна доза</dt><dd>Референтен ефективен обем за зоната, изразен в минути. По-нисък Tref означава, че еднакъв товар E създава по-голям импулс на умора. Това не е дневна препоръка или личен рекорд.</dd></div>
        <div><dt>Чувствителност</dt><dd>Безразмерен множител на непосредствената умора. По-висока стойност означава по-голям спад на товарната готовност при еднакво съотношение E/Tref.</dd></div>
        <div><dt>τ · скорост на възстановяване</dt><dd>Времева константа в дни. След τ дни остават приблизително 37% от предходната умора. По-високо τ означава по-бавно възстановяване.</dd></div>
        <div><dt>Таван на умората</dt><dd>Максималната вътрешна натрупана умора в модела. При умора над 100 видимата готовност остава 0%, но допълнителният „дълг“ се пази до този таван и удължава възстановяването.</dd></div>
      </dl>
      <p className="recovery-technical-help"><strong>Алгоритъм</strong> посочва формулата; <strong>версия параметри</strong> — набора коефициенти; <strong>fingerprint</strong> — кратък технически отпечатък, с който проверяваме, че наборът не е променен.</p>
    </div>
  </details>;
}

function RecoveryChart({ history }: { history: RecoveryHistory }) {
  const dates = [...new Set(history.daily.map((row) => row.date))];
  if (dates.length < 2) return <p className="muted-copy">Няма достатъчно дни за recovery графика.</p>;
  const width = 920, height = 300, left = 48, right = 16, top = 22, bottom = 42;
  const x = (index: number) => left + index * (width - left - right) / (dates.length - 1);
  const y = (value: number) => top + (100 - value) * (height - top - bottom) / 100;
  const threshold = history.model.practical_full_recovery_percent;
  return <figure className="history-chart recovery-chart">
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-labelledby="recovery-chart-title recovery-chart-description">
      <title id="recovery-chart-title">Динамика на товарната готовност по компоненти</title>
      <desc id="recovery-chart-description">Дневна готовност след натоварването за Z1 до Z5 и отделния силов компонент STR. Пунктираната линия е прагът за практическо пълно възстановяване.</desc>
      {[0, 50, 100].map((tick) => <g key={tick}><line className="chart-grid" x1={left} x2={width - right} y1={y(tick)} y2={y(tick)} /><text className="chart-label" x={left - 8} y={y(tick) + 4} textAnchor="end">{tick}%</text></g>)}
      <line className="chart-reference" x1={left} x2={width - right} y1={y(threshold)} y2={y(threshold)} />
      <text className="chart-label" x={width - right} y={y(threshold) - 7} textAnchor="end">практически възстановен · {decimal(threshold)}%</text>
      {ZONES.map((zone) => <polyline key={zone} className="chart-series" style={zoneStyle(zone)} points={history.daily.filter((row) => row.zone === zone).map((row) => `${x(dates.indexOf(row.date))},${y(row.readiness_after_percent)}`).join(" ")} />)}
      {history.strength && <polyline className="chart-series" style={strengthStyle} points={history.strength.daily.map((row) => `${x(dates.indexOf(row.date))},${y(row.readiness_after_percent)}`).join(" ")} />}
      <text className="chart-label" x={left} y={height - 12}>{date(dates[0])}</text><text className="chart-label" x={width - right} y={height - 12} textAnchor="end">{date(dates.at(-1)!)}</text>
    </svg>
    <figcaption className="chart-legend">{ZONES.map((zone) => <span key={zone} style={zoneStyle(zone)}><i />{zone}</span>)}{history.strength && <span style={strengthStyle}><i />STR</span>}</figcaption>
  </figure>;
}

export function RecoveryHistorySection({ history, message, refreshAvailable = false }: { history: RecoveryHistory | null; message?: string; refreshAvailable?: boolean }) {
  if (!history) return message || refreshAvailable ? <section className="history-section" aria-labelledby="recovery-title">
    <div className="section-heading"><div><p className="section-kicker">Canonical recovery</p><h2 id="recovery-title">Товарно възстановяване</h2></div></div>
    <div className="history-unavailable">
      <strong>Моделът не е премахнат.</strong>
      <p>Текущият профилен snapshot е създаден без <code>recovery-history-v1</code>. За точно възстановяване със същите canonical параметри е необходимо еднократно пълно обновяване.</p>
      {message && <small>Технически статус: {message}</small>}
    </div>
    {refreshAvailable && <div className="integration-actions"><form action="/api/integrations/intervals/refresh" method="post"><input type="hidden" name="scope" value="recovery" /><button className="action-button secondary" type="submit">Възстанови recovery модела</button></form></div>}
  </section> : null;
  return <section className="history-section recovery-section" aria-labelledby="recovery-title">
    <div className="section-heading"><div><p className="section-kicker">Canonical recovery · {date(history.period_start)} — {date(history.period_end)}</p><h2 id="recovery-title">Товарно възстановяване</h2></div><p>Предварително изчислено в Python scientific core</p></div>
    <div className="history-explainer recovery-basis"><strong>Load-only резултат</strong><p>Графиката показва остатъчната умора от тренировъчния товар. {history.wellness_diagnostics ? "Wellness историята е измерена отделно, но тази версия още не я включва във формулата за готовност." : `Последният wellness запис има ${decimal(history.wellness_coverage_percent)}% разпознати полета и не променя тази готовност.`}</p></div>
    {history.wellness_diagnostics && <WellnessCoveragePanel diagnostics={history.wellness_diagnostics} />}
    <div className="load-summary recovery-summary" role="list" aria-label="Текущо товарно възстановяване по зони">
      {history.current.map((zone) => <article key={zone.zone} className="load-summary-card" style={zoneStyle(zone.zone)} role="listitem"><div><span className="summary-zone">{zone.zone}</span><strong>{decimal(zone.readiness_percent)}%</strong><small>товарна готовност</small></div><dl><div><dt>Остатъчна умора</dt><dd>{decimal(zone.residual_fatigue)}</dd></div><div><dt>До ≥ {decimal(history.model.practical_full_recovery_percent)}%</dt><dd>{decimal(zone.days_to_practical_recovery)} дни</dd></div></dl></article>)}
    </div>
    {history.strength && <div className="load-summary strength-recovery-summary" role="list" aria-label="Текущо силово възстановяване"><article className="load-summary-card" style={strengthStyle} role="listitem"><div><span className="summary-zone">STR</span><strong>{decimal(history.strength.current.readiness_percent)}%</strong><small>силова готовност</small></div><dl><div><dt>Остатъчна умора</dt><dd>{decimal(history.strength.current.residual_fatigue)}</dd></div><div><dt>До ≥ {decimal(history.model.practical_full_recovery_percent)}%</dt><dd>{decimal(history.strength.current.days_to_practical_recovery)} дни</dd></div></dl></article><p>STR се възстановява като отделен компонент. Пулсът от силовата активност не създава втори товар в Z1–Z5.</p></div>}
    <RecoveryChart history={history} />
    <details className="recovery-settings"><summary><span><small>Read-only</small>Настройки на recovery модела</span><span className="chevron" aria-hidden="true">⌄</span></summary><RecoverySettingsHelp /><div className="activity-table-wrap"><table><thead><tr><th>Компонент</th><th>Tref</th><th>Чувствителност</th><th>τ</th><th>Таван на умората</th></tr></thead><tbody>{history.settings.map((setting) => <tr key={setting.zone}><th>{setting.zone}</th><td>{decimal(setting.tref_min)} мин</td><td>{parameter(setting.sensitivity)}</td><td>{parameter(setting.tau_days)} дни</td><td>{decimal(setting.fatigue_cap)}</td></tr>)}{history.strength && <tr><th>STR</th><td>{decimal(history.strength.settings.tref_min)} мин</td><td>{parameter(history.strength.settings.sensitivity)}</td><td>{parameter(history.strength.settings.tau_days)} дни</td><td>{decimal(history.strength.settings.fatigue_cap)}</td></tr>}</tbody></table></div><dl className="recovery-model-meta"><div><dt>Алгоритъм</dt><dd>{history.model.algorithm_version}</dd></div><div><dt>Версия параметри</dt><dd>{history.model.parameter_version}</dd></div><div><dt>Fingerprint</dt><dd>{history.model.parameter_fingerprint.slice(0, 12)}</dd></div></dl></details>
  </section>;
}
