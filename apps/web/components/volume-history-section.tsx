import type { CSSProperties } from "react";
import type { VolumeHistory, WeeklyVolume } from "../lib/volume-history";

const number = new Intl.NumberFormat("bg-BG", { maximumFractionDigits: 1 });
const decimal = (value: number) => number.format(value);
const date = (value: string) => new Intl.DateTimeFormat("bg-BG", { day: "2-digit", month: "short", timeZone: "UTC" }).format(new Date(`${value}T00:00:00Z`));
const durationStyle = { "--series": "var(--accent)" } as CSSProperties;
const zonedStyle = { "--series": "var(--zone-3)" } as CSSProperties;

function VolumeChart({ rows, periodStart, periodEnd }: { rows: WeeklyVolume[]; periodStart: string; periodEnd: string }) {
  if (rows.length < 2) return <p className="muted-copy">Няма достатъчно седмици за графика.</p>;
  const width = 920;
  const height = 300;
  const left = 48;
  const right = 16;
  const top = 22;
  const bottom = 42;
  const maximumMinutes = Math.max(...rows.flatMap((row) => [row.activity_duration_min, row.zoned_hr_time_min]));
  const yMax = Math.max(1, Math.ceil(maximumMinutes / 60));
  const ticks = [0, yMax / 2, yMax];
  const x = (index: number) => left + index * (width - left - right) / (rows.length - 1);
  const y = (minutes: number) => top + (yMax - minutes / 60) * (height - top - bottom) / yMax;
  const points = (key: "activity_duration_min" | "zoned_hr_time_min") =>
    rows.map((row, index) => `${x(index)},${y(row[key])}`).join(" ");

  return (
    <figure className="history-chart volume-chart">
      <div className="history-chart-heading">
        <div><p className="section-kicker">Календарни седмици</p><h3>Реален седмичен обем</h3></div>
        <p>Стойностите са измерени минути, показани в часове. Това не е сбор на ефективен товар E.</p>
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-labelledby="volume-chart-title volume-chart-description">
        <title id="volume-chart-title">Обща динамика на реалния тренировъчен обем</title>
        <desc id="volume-chart-description">Седмична продължителност на активностите и отделно сумата на HR-зонираното време в Z1 до Z5.</desc>
        {ticks.map((tick) => <g key={tick}>
          <line className="chart-grid" x1={left} x2={width - right} y1={y(tick * 60)} y2={y(tick * 60)} />
          <text className="chart-label" x={left - 8} y={y(tick * 60) + 4} textAnchor="end">{decimal(tick)} h</text>
        </g>)}
        <polyline className="chart-series" style={durationStyle} points={points("activity_duration_min")} />
        <polyline className="chart-series" style={zonedStyle} points={points("zoned_hr_time_min")} />
        <text className="chart-label" x={left} y={height - 12}>{date(periodStart)}</text>
        <text className="chart-label" x={width - right} y={height - 12} textAnchor="end">{date(periodEnd)}</text>
      </svg>
      <figcaption className="chart-legend">
        <span style={durationStyle}><i />Продължителност на активностите</span>
        <span style={zonedStyle}><i />HR-зонирано време Z1–Z5</span>
      </figcaption>
    </figure>
  );
}

export function VolumeHistorySection({ history, message }: { history: VolumeHistory | null; message?: string }) {
  if (!history) return message ? (
    <section className="history-section" aria-labelledby="volume-title">
      <div className="section-heading"><div><p className="section-kicker">Реален обем</p><h2 id="volume-title">Обща динамика на реалния обем</h2></div></div>
      <p className="history-unavailable">{message}</p>
    </section>
  ) : null;

  return (
    <section className="history-section" aria-labelledby="volume-title">
      <div className="section-heading">
        <div><p className="section-kicker">{date(history.period_start)} — {date(history.period_end)}</p><h2 id="volume-title">Обща динамика на реалния обем</h2></div>
        <p>{history.quality.modeled_activities} моделирани активности · {history.weekly.length} календарни седмици</p>
      </div>
      <div className="history-explainer">
        <strong>Две различни мерки</strong>
        <p>Продължителността използва наличната стойност от активността. HR-зонираното време е точната сума на реалните минути T<sub>z</sub> в Z1–Z5. Линиите не се сумират; отделният STR компонент от пълния план все още не е интегриран.</p>
      </div>
      {history.quality.missing_duration_activities > 0 && <p className="volume-quality-note">За {history.quality.missing_duration_activities} активности липсва обща продължителност; те остават в HR-зонирания обем и са изключени само от линията за продължителност.</p>}
      <VolumeChart rows={history.weekly} periodStart={history.period_start} periodEnd={history.period_end} />
      <p className="report-note">Договор: {history.schema_version} · агрегация: {history.model.aggregation_version} · източник: {history.model.source_schema_version}</p>
    </section>
  );
}
