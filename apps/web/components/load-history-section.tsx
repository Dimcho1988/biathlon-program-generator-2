import type { CSSProperties } from "react";
import type { DailyZoneLoad, LoadHistory } from "../lib/load-history";
import { ZONES, type Zone } from "../lib/training-status";

const number = new Intl.NumberFormat("bg-BG", { maximumFractionDigits: 1 });
const decimal = (value: number) => number.format(value);
const date = (value: string) => new Intl.DateTimeFormat("bg-BG", { day: "2-digit", month: "short", timeZone: "UTC" }).format(new Date(`${value}T00:00:00Z`));

const zoneStyle = (zone: Zone): CSSProperties => ({ "--series": `var(--zone-${ZONES.indexOf(zone) + 1})` } as CSSProperties);

function SevenFortyChart({ rows }: { rows: DailyZoneLoad[] }) {
  const dates = [...new Set(rows.map((row) => row.date))];
  if (dates.length < 2) return <p className="muted-copy">Няма достатъчно дни за графика.</p>;
  const width = 920;
  const height = 300;
  const left = 48;
  const right = 16;
  const top = 22;
  const bottom = 42;
  const values = rows.map((row) => row.status_7_40);
  const yMin = Math.min(0.6, Math.floor(Math.min(...values) * 10) / 10);
  const yMax = Math.max(1.4, Math.ceil(Math.max(...values) * 10) / 10);
  const x = (index: number) => left + index * (width - left - right) / (dates.length - 1);
  const y = (value: number) => top + (yMax - value) * (height - top - bottom) / (yMax - yMin);

  return (
    <figure className="history-chart">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-labelledby="load-chart-title load-chart-description">
        <title id="load-chart-title">Динамика на индекса 7/40 по зони</title>
        <desc id="load-chart-description">Линии за Z1 до Z5 през наличния период. Пунктираната линия отбелязва индекс едно.</desc>
        {[yMin, 1, yMax].map((tick) => <g key={tick}>
          <line className={tick === 1 ? "chart-reference" : "chart-grid"} x1={left} x2={width - right} y1={y(tick)} y2={y(tick)} />
          <text className="chart-label" x={left - 8} y={y(tick) + 4} textAnchor="end">{decimal(tick)}</text>
        </g>)}
        {ZONES.map((zone) => {
          const zoneRows = rows.filter((row) => row.zone === zone);
          const points = zoneRows.map((row) => `${x(dates.indexOf(row.date))},${y(row.status_7_40)}`).join(" ");
          return <polyline key={zone} className="chart-series" style={zoneStyle(zone)} points={points} />;
        })}
        <text className="chart-label" x={left} y={height - 12}>{date(dates[0])}</text>
        <text className="chart-label" x={width - right} y={height - 12} textAnchor="end">{date(dates.at(-1)!)}</text>
      </svg>
      <figcaption className="chart-legend">{ZONES.map((zone) => <span key={zone} style={zoneStyle(zone)}><i />{zone}</span>)}</figcaption>
    </figure>
  );
}

function EffectiveLoadChart({ rows }: { rows: DailyZoneLoad[] }) {
  const dates = [...new Set(rows.map((row) => row.date))];
  if (dates.length < 2) return <p className="muted-copy">Няма достатъчно дни за графика.</p>;
  const width = 920;
  const height = 300;
  const left = 48;
  const right = 16;
  const top = 22;
  const bottom = 42;
  const maximum = Math.max(...rows.map((row) => row.effective_load));
  const yMax = Math.max(1, Math.ceil(maximum));
  const ticks = [0, yMax / 2, yMax];
  const x = (index: number) => left + index * (width - left - right) / (dates.length - 1);
  const y = (value: number) => top + (yMax - value) * (height - top - bottom) / yMax;

  return (
    <figure className="history-chart">
      <div className="history-chart-heading">
        <div><p className="section-kicker">Канонични дневни стойности</p><h3>Дневен ефективен товар E по зони</h3></div>
        <p>Показани са Z1–Z5 поотделно; линиите не се сумират до нов общ резултат.</p>
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-labelledby="effective-load-chart-title effective-load-chart-description">
        <title id="effective-load-chart-title">Дневен ефективен товар E по зони</title>
        <desc id="effective-load-chart-description">Директните дневни effective load стойности за Z1 до Z5 през наличния период, без изглаждане или сумиране.</desc>
        {ticks.map((tick) => <g key={tick}>
          <line className="chart-grid" x1={left} x2={width - right} y1={y(tick)} y2={y(tick)} />
          <text className="chart-label" x={left - 8} y={y(tick) + 4} textAnchor="end">{decimal(tick)}</text>
        </g>)}
        {ZONES.map((zone) => {
          const zoneRows = rows.filter((row) => row.zone === zone);
          const points = zoneRows.map((row) => `${x(dates.indexOf(row.date))},${y(row.effective_load)}`).join(" ");
          return <polyline key={zone} className="chart-series" style={zoneStyle(zone)} points={points} />;
        })}
        <text className="chart-label" x={left} y={height - 12}>{date(dates[0])}</text>
        <text className="chart-label" x={width - right} y={height - 12} textAnchor="end">{date(dates.at(-1)!)}</text>
      </svg>
      <figcaption className="chart-legend">{ZONES.map((zone) => <span key={zone} style={zoneStyle(zone)}><i />{zone}</span>)}</figcaption>
    </figure>
  );
}

export function LoadHistorySection({ history, message }: { history: LoadHistory | null; message?: string }) {
  if (!history) return message ? (
    <section className="history-section" aria-labelledby="history-title">
      <div className="section-heading"><div><p className="section-kicker">90-дневен прозорец</p><h2 id="history-title">Натоварване и динамика</h2></div></div>
      <p className="history-unavailable">{message} Обновете реалните данни след публикуването на новата API версия.</p>
    </section>
  ) : null;

  return (
    <section className="history-section" aria-labelledby="history-title">
      <div className="section-heading">
        <div><p className="section-kicker">{date(history.period_start)} — {date(history.period_end)}</p><h2 id="history-title">Натоварване и динамика</h2></div>
        <p>{history.quality.processed_activities} обработени активности · {history.quality.no_activity_days} дни без активност</p>
      </div>

      <div className="history-explainer">
        <strong>Как се чете 7/40</strong>
        <p>Индексът сравнява средния ефективен товар за последните 7 и 40 календарни дни със стабилизираща база. Деветдесетте дни осигуряват историческото загряване; те не са знаменател на индекса.</p>
      </div>

      <div className="load-summary" role="list" aria-label="Текущи показатели по зони">
        {history.zones.map((zone) => <article key={zone.zone} className={`load-summary-card ${zone.zone.toLowerCase()}`} style={zoneStyle(zone.zone)} role="listitem">
          <div><span className="summary-zone">{zone.zone}</span><strong>{decimal(zone.status_7_40)}</strong><small>7/40</small></div>
          <dl>
            <div><dt>E7 / ден</dt><dd>{decimal(zone.e7_daily)}</dd></div>
            <div><dt>E40 / ден</dt><dd>{decimal(zone.e40_daily)}</dd></div>
            <div><dt>Tref</dt><dd>{decimal(zone.tref_min)} мин</dd></div>
          </dl>
        </article>)}
      </div>

      <SevenFortyChart rows={history.daily} />

      <EffectiveLoadChart rows={history.daily} />

      <div className="activities-heading"><div><p className="section-kicker">Последни сесии</p><h3>Реално → приравнено → ефективно</h3></div><p>{history.quality.limited_activities} с ограничено HR покритие · {history.quality.excluded_activities} изключени</p></div>
      <div className="activity-list">
        {history.activities.slice(0, 12).map((activity) => <details key={activity.activity_ref} className="activity-row">
          <summary>
            <span><strong>{activity.sport}</strong><small>{date(activity.date)}</small></span>
            <span>{activity.duration_min === null ? "—" : `${decimal(activity.duration_min)} мин`}</span>
            <span className={activity.quality_status === "limited" ? "quality-limited" : "quality-valid"}>{decimal(activity.hr_coverage_percent)}% HR</span>
            <span className="chevron" aria-hidden="true">⌄</span>
          </summary>
          <div className="activity-table-wrap"><table>
            <thead><tr><th>Зона</th><th>Реално</th><th>Приравнено</th><th>Ефективно E</th><th>Среден HR</th><th>Стойност/мин</th></tr></thead>
            <tbody>{activity.zones.map((zone) => <tr key={zone.zone}>
              <th>{zone.zone}</th><td>{decimal(zone.raw_time_min)} мин</td><td>{decimal(zone.equivalent_time_min)} мин</td><td>{decimal(zone.effective_load)}</td><td>{zone.mean_effective_hr_bpm === null ? "—" : `${decimal(zone.mean_effective_hr_bpm)} bpm`}</td><td>{zone.average_minute_value_percent === null ? "—" : `${decimal(zone.average_minute_value_percent)}%`}</td>
            </tr>)}</tbody>
          </table></div>
        </details>)}
      </div>
    </section>
  );
}
