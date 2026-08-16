import type { CSSProperties } from "react";
import type { RecoveryHistory } from "../lib/recovery-history";
import { ZONES, type Zone } from "../lib/training-status";

const number = new Intl.NumberFormat("bg-BG", { maximumFractionDigits: 1 });
const decimal = (value: number) => number.format(value);
const date = (value: string) => new Intl.DateTimeFormat("bg-BG", { day: "2-digit", month: "short", timeZone: "UTC" }).format(new Date(`${value}T00:00:00Z`));
const zoneStyle = (zone: Zone): CSSProperties => ({ "--series": `var(--zone-${ZONES.indexOf(zone) + 1})` } as CSSProperties);

function RecoveryChart({ history }: { history: RecoveryHistory }) {
  const dates = [...new Set(history.daily.map((row) => row.date))];
  if (dates.length < 2) return <p className="muted-copy">Няма достатъчно дни за recovery графика.</p>;
  const width = 920, height = 300, left = 48, right = 16, top = 22, bottom = 42;
  const x = (index: number) => left + index * (width - left - right) / (dates.length - 1);
  const y = (value: number) => top + (100 - value) * (height - top - bottom) / 100;
  const threshold = history.model.practical_full_recovery_percent;
  return <figure className="history-chart recovery-chart">
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-labelledby="recovery-chart-title recovery-chart-description">
      <title id="recovery-chart-title">Динамика на товарната готовност по зони</title>
      <desc id="recovery-chart-description">Дневна готовност след натоварването за Z1 до Z5. Пунктираната линия е прагът за практическо пълно възстановяване.</desc>
      {[0, 50, 100].map((tick) => <g key={tick}><line className="chart-grid" x1={left} x2={width - right} y1={y(tick)} y2={y(tick)} /><text className="chart-label" x={left - 8} y={y(tick) + 4} textAnchor="end">{tick}%</text></g>)}
      <line className="chart-reference" x1={left} x2={width - right} y1={y(threshold)} y2={y(threshold)} />
      <text className="chart-label" x={width - right} y={y(threshold) - 7} textAnchor="end">практически възстановен · {decimal(threshold)}%</text>
      {ZONES.map((zone) => <polyline key={zone} className="chart-series" style={zoneStyle(zone)} points={history.daily.filter((row) => row.zone === zone).map((row) => `${x(dates.indexOf(row.date))},${y(row.readiness_after_percent)}`).join(" ")} />)}
      <text className="chart-label" x={left} y={height - 12}>{date(dates[0])}</text><text className="chart-label" x={width - right} y={height - 12} textAnchor="end">{date(dates.at(-1)!)}</text>
    </svg>
    <figcaption className="chart-legend">{ZONES.map((zone) => <span key={zone} style={zoneStyle(zone)}><i />{zone}</span>)}</figcaption>
  </figure>;
}

export function RecoveryHistorySection({ history, message }: { history: RecoveryHistory | null; message?: string }) {
  if (!history) return message ? <section className="history-section" aria-labelledby="recovery-title"><div className="section-heading"><div><p className="section-kicker">Canonical recovery</p><h2 id="recovery-title">Товарно възстановяване</h2></div></div><p className="history-unavailable">{message} Обновете реалните данни след публикуването на recovery API версията.</p></section> : null;
  return <section className="history-section recovery-section" aria-labelledby="recovery-title">
    <div className="section-heading"><div><p className="section-kicker">Canonical recovery · {date(history.period_start)} — {date(history.period_end)}</p><h2 id="recovery-title">Товарно възстановяване</h2></div><p>Предварително изчислено в Python scientific core</p></div>
    <div className="history-explainer recovery-basis"><strong>Load-only резултат</strong><p>Графиката показва остатъчната умора от тренировъчния товар. Wellness покритието е {decimal(history.wellness_coverage_percent)}% и не променя тази готовност.</p></div>
    <div className="load-summary recovery-summary" role="list" aria-label="Текущо товарно възстановяване по зони">
      {history.current.map((zone) => <article key={zone.zone} className="load-summary-card" style={zoneStyle(zone.zone)} role="listitem"><div><span className="summary-zone">{zone.zone}</span><strong>{decimal(zone.readiness_percent)}%</strong><small>товарна готовност</small></div><dl><div><dt>Остатъчна умора</dt><dd>{decimal(zone.residual_fatigue)}</dd></div><div><dt>До ≥ {decimal(history.model.practical_full_recovery_percent)}%</dt><dd>{decimal(zone.days_to_practical_recovery)} дни</dd></div></dl></article>)}
    </div>
    <RecoveryChart history={history} />
    <details className="recovery-settings"><summary><span><small>Read-only</small>Настройки на recovery модела</span><span className="chevron" aria-hidden="true">⌄</span></summary><div className="activity-table-wrap"><table><thead><tr><th>Зона</th><th>Tref</th><th>Чувствителност</th><th>τ</th><th>Таван на умората</th></tr></thead><tbody>{history.settings.map((setting) => <tr key={setting.zone}><th>{setting.zone}</th><td>{decimal(setting.tref_min)} мин</td><td>{decimal(setting.sensitivity)}</td><td>{decimal(setting.tau_days)} дни</td><td>{decimal(setting.fatigue_cap)}</td></tr>)}</tbody></table></div><dl className="recovery-model-meta"><div><dt>Алгоритъм</dt><dd>{history.model.algorithm_version}</dd></div><div><dt>Версия параметри</dt><dd>{history.model.parameter_version}</dd></div><div><dt>Fingerprint</dt><dd>{history.model.parameter_fingerprint.slice(0, 12)}</dd></div></dl></details>
  </section>;
}
