"use client";

import { useId, type CSSProperties } from "react";
import { displayDate, shiftDate } from "../lib/dashboard-periods";
import type { LoadChartRow } from "../lib/load-dynamics";
import { ZONES } from "../lib/training-status";

export type LoadSeries = { source: "hr" | "speed"; rows: LoadChartRow[] };
const decimal = (value: number) => value.toLocaleString("bg-BG", { maximumFractionDigits: 1 });

export function LoadSourceChart({ series, metric, start, end }: {
  series: LoadSeries[]; metric: "ratio" | "effective"; start: string; end: string;
}) {
  const id = useId();
  const ratio = metric === "ratio", comparison = series.length > 1;
  const title = ratio ? "Динамика на индекса 7/40 по зони" : "Дневен ефективен товар E по зони";
  const days = Math.round((Date.parse(end) - Date.parse(start)) / 86_400_000);
  const visible = series.map(s => ({ ...s, rows: s.rows.filter(row => row.date >= start && row.date <= end) }));
  const values = visible.flatMap(s => s.rows.flatMap(row => row[metric] === null ? [] : [row[metric]!]));
  if (days < 1 || !values.length) return <p className="muted-copy">Няма достатъчно данни за графиката „{title}“.</p>;
  const low = ratio ? Math.min(.6, Math.floor(Math.min(...values) * 10) / 10) : 0;
  const high = ratio ? Math.max(1.4, Math.ceil(Math.max(...values) * 10) / 10) : Math.max(1, Math.ceil(Math.max(...values)));
  const ticks = ratio ? [low, 1, high] : [0, high / 2, high];
  const x = (day: string) => 48 + (Date.parse(day) - Date.parse(start)) / 86_400_000 / days * 856;
  const y = (value: number) => 22 + (high - value) / (high - low) * 236;

  return <figure className="history-chart load-source-chart">
    <div className="history-chart-heading">
      <div><p className="section-kicker">{comparison ? "Пулс и скорост · обща скала" : "По приравнена скорост"}</p><h3>{title}</h3></div>
      <p>{ratio ? "Над 1 — покачване; под 1 — спад на ефективния товар." : "Дневни стойности по зони; двата вида товар не се събират."}</p>
    </div>
    <svg viewBox="0 0 920 300" role="img" aria-labelledby={`${id}-title ${id}-description`}>
      <title id={`${id}-title`}>{title} · {comparison ? "сравнение пулс и скорост" : "по скорост"}</title>
      <desc id={`${id}-description`}>Z1 до Z5. {comparison ? "Плътна линия за пулс и прекъсната за скорост върху еднакви дати и скала." : "Товар, изчислен от Vflat."} Липсващите дни не се свързват.</desc>
      {ticks.map(tick => <g key={tick}><line className={ratio && tick === 1 ? "chart-reference" : "chart-grid"} x1="48" x2="904" y1={y(tick)} y2={y(tick)}/><text className="chart-label" x="40" y={y(tick) + 4} textAnchor="end">{decimal(tick)}</text></g>)}
      {visible.flatMap(s => ZONES.map((zone, index) => {
        const rows = s.rows.filter(row => row.zone === zone).sort((a, b) => a.date.localeCompare(b.date));
        let previous: LoadChartRow | null = null;
        const path = rows.map(row => {
          if (row[metric] === null) { previous = null; return ""; }
          const command = previous && shiftDate(previous.date, 1) === row.date ? "L" : "M";
          previous = row;
          return `${command}${x(row.date)},${y(row[metric]!)}`;
        }).join(" ");
        return <path key={`${s.source}:${zone}`} data-source={s.source} data-zone={zone} className="chart-series" style={{ "--series": `var(--zone-${index + 1})` } as CSSProperties} strokeDasharray={comparison && s.source === "speed" ? "7 5" : undefined} d={path}><title>{zone} · {s.source === "hr" ? "По пулс" : "По скорост"}</title></path>;
      }))}
      <text className="chart-label" x="48" y="288">{displayDate(start)}</text><text className="chart-label" x="904" y="288" textAnchor="end">{displayDate(end)}</text>
    </svg>
    <figcaption>
      <div className="chart-legend">{ZONES.map((zone, i) => <span key={zone} style={{ "--series": `var(--zone-${i + 1})` } as CSSProperties}><i/>{zone}</span>)}</div>
      {comparison && <div className="chart-legend load-source-legend"><span><i/>По пулс</span><span><i className="speed-line"/>По скорост</span></div>}
    </figcaption>
  </figure>;
}
