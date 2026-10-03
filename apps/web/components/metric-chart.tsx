"use client";

import { useId, useMemo, useState, type PointerEvent } from "react";
import { chartDomain, chartPath, chartTicks, nearestChartPoint, spacedChartTicks, type ChartPoint } from "../lib/chart-geometry";
import { durationSeconds } from "../lib/duration-format";
import { useChartLayout } from "./chart-layout";

export type MetricSeries = {
  key: string; label: string; color: string; points: ChartPoint[];
  dashed?: boolean; source?: string; zone?: string; kind?: "bar" | "line";
};
type Props = {
  title: string; series: MetricSeries[]; xKind?: "date" | "duration"; unit: string;
  yKind?: "number" | "duration" | "percent"; domain?: [number, number];
  reference?: { value: number; label: string }; maxGap?: number; zero?: boolean;
  description?: string; initialIndex?: number; onSelect?: (index: number) => void;
  selectedIndex?: number; controls?: boolean;
  xLabel?: string;
};
const numeric = (value: number) => value.toLocaleString("bg-BG", { maximumFractionDigits: 2 });
const date = (value: number) => new Date(value).toLocaleDateString("bg-BG", { day: "2-digit", month: "2-digit", timeZone: "UTC" });

export function MetricChart({ title, series, xKind = "date", unit, yKind = "number", domain, reference, maxGap, zero = false, description, initialIndex, onSelect, selectedIndex, controls = true, xLabel }: Props) {
  const id = useId();
  const [hidden, setHidden] = useState<string[]>([]);
  const [position, setPosition] = useState(initialIndex ?? -1);
  const active = series.filter(item => !hidden.includes(item.key));
  const format = (value: number) => yKind === "duration" ? durationSeconds(value) : `${numeric(value)}${yKind === "percent" ? "%" : ""}`;
  const xFormat = xKind === "date" ? date : durationSeconds;
  const dates = useMemo(() => [...new Set(series.flatMap(item => item.points.filter(point => Number.isFinite(point.x)).map(point => point.x)))].sort((a, b) => a - b).map(x => ({ x })), [series]);
  const bounds = domain ?? chartDomain([...series.flatMap(item => item.points.map(point => point.y)), reference?.value ?? null], zero);
  const ticks = chartTicks(...bounds, 5, yKind === "duration");
  const left = Math.max(50, ...ticks.map(value => format(value).length * 7 + 13));
  const layout = useChartLayout(left);
  const { ref, width, height, right, top, bottom, plotWidth, plotHeight, compact } = layout;
  const start = dates[0]?.x ?? 0, end = dates.at(-1)?.x ?? 1;
  const barSeries = active.filter(item => item.kind === "bar");
  const barWidth = Math.min(26, plotWidth / Math.max(dates.length, 1) * .72 / Math.max(barSeries.length, 1));
  const inset = barSeries.length ? barWidth * barSeries.length / 2 + 2 : 0;
  const x = (value: number) => dates.length === 1 ? left + plotWidth / 2 : left + inset + (value - start) / Math.max(1, end - start) * (plotWidth - inset * 2);
  const y = (value: number) => bottom - (value - bounds[0]) / (bounds[1] - bounds[0]) * plotHeight;
  const current = Math.max(0, Math.min(dates.length - 1, selectedIndex ?? (position < 0 ? dates.length - 1 : position)));
  const selectedX = dates[current]?.x;
  const readout = active.map(item => ({ ...item, point: item.points.find(point => point.x === selectedX) }));
  const change = (index: number) => { setPosition(index); onSelect?.(index); };
  const move = (event: PointerEvent<SVGSVGElement>) => {
    if (!dates.length || (event.pointerType === "touch" && event.type === "pointermove" && !event.buttons)) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const pixel = (event.clientX - rect.left) * width / rect.width;
    const value = start + Math.max(0, Math.min(1, (pixel - left - inset) / (plotWidth - inset * 2))) * (end - start);
    change(nearestChartPoint(dates, value));
  };
  const tickCount = Math.min(dates.length, compact ? 3 : 5);
  const xTicks = xKind === "duration" && dates.length > 1
    ? spacedChartTicks([...new Set([start,...chartTicks(start,end,compact ? 3 : 5,true),end])],x).map(x=>({x}))
    : Array.from({ length: tickCount }, (_, i) => dates[Math.round(i * (dates.length - 1) / Math.max(1, tickCount - 1))]);
  if (!dates.length || !series.some(item => item.points.some(point => point.y !== null && Number.isFinite(point.y)))) return <p className="detail-empty">Няма налични измервания за „{title}“. Липсващите стойности не се приемат за нула.</p>;

  return <div className="metric-chart">
    {controls && series.length > 1 && <div className="metric-chart-controls" role="group" aria-label={`Серии · ${title}`}>
      {series.map(item => <button key={item.key} type="button" aria-pressed={!hidden.includes(item.key)} onClick={() => setHidden(previous => previous.includes(item.key) ? previous.filter(key => key !== item.key) : [...previous, item.key])}><i style={{ borderColor: item.color, borderTopStyle: item.dashed ? "dashed" : "solid" }} />{item.label}</button>)}
    </div>}
    <div className="chart-frame" ref={ref}>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={title} aria-describedby={`${id}-description`} onPointerDown={move} onPointerMove={event => { if (event.buttons || (!onSelect && event.pointerType !== "touch")) move(event); }}>
        <title>{title}</title><desc id={`${id}-description`}>{description ?? `${unit}. Избери точка или използвай плъзгача под графиката за точните стойности. Прекъсванията означават липсващи измервания.`}</desc>
        <text className="metric-axis-unit" x={left} y="17">{unit}</text>
        {ticks.map(tick => <g key={tick}><line className="chart-grid" x1={left} x2={right} y1={y(tick)} y2={y(tick)} /><text className="chart-label" x={left - 9} y={y(tick) + 4} textAnchor="end">{format(tick)}</text></g>)}
        {reference && reference.value >= bounds[0] && reference.value <= bounds[1] && <line className="chart-reference" x1={left} x2={right} y1={y(reference.value)} y2={y(reference.value)}><title>{reference.label}</title></line>}
        {xTicks.map((tick, i) => <g key={tick.x}><line className="metric-tick" x1={x(tick.x)} x2={x(tick.x)} y1={bottom} y2={bottom + 5} /><text className="chart-label" x={x(tick.x)} y={bottom + 22} textAnchor={i === 0 ? "start" : i === xTicks.length - 1 ? "end" : "middle"}>{xFormat(tick.x)}</text></g>)}
        {active.map(item => item.kind === "bar" ? <g key={item.key}>{item.points.map(point => point.y === null ? null : <rect key={point.x} x={Math.max(left, Math.min(right - barWidth, x(point.x) + (barSeries.indexOf(item) - (barSeries.length - 1) / 2) * barWidth - barWidth / 2))} y={y(point.y)} width={barWidth * .9} height={Math.max(0, bottom - y(point.y))} rx="2" fill={item.color} opacity={point.x === selectedX ? 1 : .7}><title>{`${xFormat(point.x)} · ${item.label}: ${format(point.y)}`}</title></rect>)}</g> : <g key={item.key}>
          <path data-source={item.source} data-zone={item.zone} d={chartPath(item.points, x, y, maxGap)} fill="none" stroke={item.color} strokeWidth="2.4" strokeDasharray={item.dashed ? "7 5" : undefined} />
          {item.points.map((point,i) => {
            if (point.y === null || !Number.isFinite(point.y)) return null;
            const previous=item.points[i-1], next=item.points[i+1];
            const isolated=(!previous || previous.y===null || point.breakBefore || point.x-previous.x>(maxGap??Infinity)) && (!next || next.y===null || next.breakBefore || next.x-point.x>(maxGap??Infinity));
            return item.points.length <= 100 || isolated ? <circle key={point.x} cx={x(point.x)} cy={y(point.y)} r="2.5" fill={point.partial ? "var(--surface)" : item.color} stroke={item.color}><title>{`${xFormat(point.x)} · ${item.label}: ${format(point.y)}`}</title></circle> : null;
          })}
        </g>)}
        {selectedX !== undefined && <line className="metric-cursor" x1={x(selectedX)} x2={x(selectedX)} y1={top} y2={bottom} />}
        {readout.map(item => item.kind === "bar" || item.point?.y == null ? null : <circle key={item.key} cx={x(selectedX!)} cy={y(item.point.y)} r="4" fill="var(--surface)" stroke={item.color} strokeWidth="2.5" />)}
      </svg>
    </div>
    {reference && <p className="metric-chart-reference">Пунктиран праг: {reference.label}</p>}
    <div className="metric-chart-readout"><strong>{xLabel ?? (xKind === "date" ? "Дата" : "Време")}: {xKind === "date" ? new Date(selectedX ?? start).toLocaleDateString("bg-BG", {timeZone:"UTC"}) : xFormat(selectedX ?? start)}</strong><dl>{readout.map(item => <div key={item.key}><dt><i style={{ background: item.color }} />{item.label}</dt><dd>{item.point?.y == null ? "Няма данни" : `${format(item.point.y)}${yKind === "number" && unit !== "Индекс 7/40" && unit !== "Ефективен товар E" ? ` ${unit}` : ""}`}</dd></div>)}</dl>{active.length === 0 && <p>Избери поне една серия.</p>}</div>
    <label className="metric-chart-scrubber">{xKind === "date" ? "Разгледай по дати" : "Разгледай във времето"}<input type="range" min="0" max={Math.max(0, dates.length - 1)} step="1" value={current} disabled={dates.length < 2} aria-label={`Разгледай · ${title}`} aria-valuetext={`${xFormat(selectedX ?? start)} · ${readout.map(item => `${item.label}: ${item.point?.y == null ? "Няма данни" : format(item.point.y)}`).join("; ")}`} onChange={event => change(Number(event.target.value))} /></label>
  </div>;
}
