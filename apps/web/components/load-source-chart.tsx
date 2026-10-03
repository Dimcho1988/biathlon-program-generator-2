"use client";

import { type CSSProperties } from "react";

import type { LoadChartRow } from "../lib/load-dynamics";
import { ZONES } from "../lib/training-status";

export type LoadSeries = { source: "hr" | "speed"; rows: LoadChartRow[] };
import { MetricChart } from "./metric-chart";

export function LoadSourceChart({ series, metric, start, end }: {
  series: LoadSeries[]; metric: "ratio" | "effective"; start: string; end: string;
}) {
  const ratio = metric === "ratio", comparison = series.length > 1;
  const title = ratio ? "Динамика на индекса 7/40 по зони" : "Дневен ефективен товар E по зони";
  const days = Math.round((Date.parse(end) - Date.parse(start)) / 86_400_000);
  const visible = series.map(s => ({ ...s, rows: s.rows.filter(row => row.date >= start && row.date <= end) }));
  const values = visible.flatMap(s => s.rows.flatMap(row => row[metric] === null ? [] : [row[metric]!]));
  if (days < 1 || !values.length) return <p className="muted-copy">Няма достатъчно данни за графиката „{title}“.</p>;
  const low = ratio ? Math.min(.6, Math.floor(Math.min(...values) * 10) / 10) : 0;
  const high = ratio ? Math.max(1.4, Math.ceil(Math.max(...values) * 10) / 10) : Math.max(1, Math.ceil(Math.max(...values)));

  return <figure className="history-chart load-source-chart">
    <div className="history-chart-heading">
      <div><p className="section-kicker">{comparison ? "Пулс и скорост · обща скала" : "По приравнена скорост"}</p><h3>{title}</h3></div>
      <p>{ratio ? "Над 1 — покачване; под 1 — спад на ефективния товар." : "Дневни стойности по зони; двата вида товар не се събират."}</p>
    </div>
    <MetricChart title={title} unit={ratio ? "Индекс 7/40" : "Ефективен товар E"} domain={[low, high]} maxGap={86400000} reference={ratio ? { value: 1, label: "7/40 = 1 · стабилен товар" } : undefined} series={visible.flatMap(s => ZONES.map((zone, index) => ({
      key: `${s.source}:${zone}`, source: s.source, zone, label: comparison ? `${zone} · ${s.source === "hr" ? "Пулс" : "Скорост"}` : zone,
      color: `var(--zone-${index + 1})`, dashed: comparison && s.source === "speed",
      points: s.rows.filter(row => row.zone === zone).sort((a,b) => a.date.localeCompare(b.date)).map(row => ({ x: Date.parse(row.date), y: row[metric] })),
    })))} />
    <figcaption>
      <div className="chart-legend">{ZONES.map((zone, i) => <span key={zone} style={{ "--series": `var(--zone-${i + 1})` } as CSSProperties}><i/>{zone}</span>)}</div>
      {comparison && <div className="chart-legend load-source-legend"><span><i/>По пулс</span><span><i className="speed-line"/>По скорост</span></div>}
    </figcaption>
  </figure>;
}
