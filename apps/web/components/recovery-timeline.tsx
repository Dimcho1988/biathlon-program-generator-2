"use client";

import { useState, type CSSProperties, type PointerEvent } from "react";
import { MODEL_ZONES, type ModelZone, type RecoveryV2 } from "../lib/models";
import { dateAtOffset, forecastReadiness, recoveryForecastPoints, recoveryHistorySegments, stepRecoveryCursor } from "../lib/recovery-timeline";
import "./recovery-timeline.css";

const number = new Intl.NumberFormat("bg-BG", { maximumFractionDigits: 2 });
const shortDate = (date: string) => new Intl.DateTimeFormat("bg-BG", { day: "2-digit", month: "2-digit", timeZone: "UTC" }).format(new Date(`${date}T00:00:00Z`));
const color = (zone: ModelZone) => zone === "STR" ? "var(--strength)" : `var(--zone-${zone.slice(1)})`;
const style = (zone: ModelZone): CSSProperties => ({ "--series": color(zone) } as CSSProperties);
const x = (day: number) => 56 + (day + 5) * 824 / 7;
const y = (value: number) => 264 - value * 2.12;

export function RecoveryTimeline({ history }: { history: RecoveryV2 }) {
  const [visible, setVisible] = useState<ModelZone[]>([...MODEL_ZONES]);
  const [cursor, setCursor] = useState(0);
  const series = MODEL_ZONES.map(zone => ({
    zone,
    past: recoveryHistorySegments(history, zone),
    future: recoveryForecastPoints(history, zone),
  }));
  const shown = series.filter(row => visible.includes(row.zone));
  const snap = (day: number) => day < 0 ? Math.round(day) : Math.round(day * 24) / 24;
  function move(event: PointerEvent<SVGSVGElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    const position = (event.clientX - rect.left) * 920 / rect.width;
    setCursor(snap(Math.max(-5, Math.min(2, (position - 56) * 7 / 824 - 5))));
  }
  function toggle(zone: ModelZone) {
    setVisible(previous => previous.includes(zone) ? previous.filter(value => value !== zone) : MODEL_ZONES.filter(value => value === zone || previous.includes(value)));
  }
  const cursorLabel = cursor === 0 ? `Днес · ${shortDate(history.as_of)}`
    : cursor < 0 ? `${shortDate(dateAtOffset(history.as_of, cursor))} · отчетен товар`
    : `${shortDate(dateAtOffset(history.as_of, cursor))} · прогноза +${number.format(cursor * 24)} ч`;

  return <figure className="history-chart recovery-timeline">
    <div className="history-chart-heading"><div><h3>Възстановяване · история и прогноза</h3><p>5 дни назад · днес · 2 дни напред</p></div></div>
    <div className="recovery-zone-controls" role="group" aria-label="Зони на графиката">
      <button type="button" onClick={() => setVisible([...MODEL_ZONES])} aria-pressed={visible.length === MODEL_ZONES.length}>Всички</button>
      {MODEL_ZONES.map(zone => <button type="button" key={zone} style={style(zone)} aria-pressed={visible.includes(zone)} onClick={() => toggle(zone)}><i aria-hidden="true" />{zone}</button>)}
    </div>
    <div className="recovery-chart-scroll">
      <svg viewBox="0 0 920 316" role="img" aria-label="Възстановяване по зони: пет дни история и два дни прогноза" onPointerMove={move} onPointerLeave={() => setCursor(0)}>
        <title>Възстановяване по зони — обща времева ос</title>
        <desc>Плътните линии показват дневната готовност след отчетения товар. Пунктираните линии са прогноза без нови тренировки. Хоризонталният праг е 90%. Липсващите исторически дни са прекъсвания.</desc>
        <rect className="recovery-forecast-area" x={x(0)} y="40" width={x(2) - x(0)} height="224" />
        {[0, 25, 50, 75, 90, 100].map(value => <g key={value}><line className={value === 90 ? "recovery-ready-line" : "chart-grid"} x1={x(-5)} x2={x(2)} y1={y(value)} y2={y(value)} /><text className="chart-label" x="44" y={y(value) + 4} textAnchor="end">{value}%</text></g>)}
        <text className="chart-label" x={x(-5)} y="24">История</text><text className="chart-label" x={(x(0) + x(2)) / 2} y="24" textAnchor="middle">Прогноза</text>
        <line className="recovery-today-line" x1={x(0)} x2={x(0)} y1="40" y2="264" />
        {shown.map(row => <g key={row.zone} style={style(row.zone)} data-recovery-zone={row.zone}>
          {row.past.map((segment, index) => <g key={index}><polyline className="chart-series" points={segment.map(point => `${x(point.day)},${y(point.readiness)}`).join(" ")} />{segment.map(point => <circle key={point.day} cx={x(point.day)} cy={y(point.readiness)} r="3" fill={color(row.zone)}><title>{`${row.zone} · ${shortDate(dateAtOffset(history.as_of, point.day))} · ${number.format(point.readiness)}%`}</title></circle>)}</g>)}
          <polyline className="chart-series recovery-forecast-series" points={row.future.map(point => `${x(point.day)},${y(point.readiness)}`).join(" ")} />
          {[0, 1, 2].map(day => { const value = forecastReadiness(row.future, day); return value === null ? null : <circle key={day} cx={x(day)} cy={y(value)} r="3.5" fill="var(--surface)" stroke={color(row.zone)} strokeWidth="2"><title>{`${row.zone} · ${day === 0 ? "днес" : `+${day * 24} ч`} · ${number.format(value)}%`}</title></circle>; })}
        </g>)}
        {Array.from({ length: 8 }, (_, index) => index - 5).map(day => <g key={day}><line className="chart-grid" x1={x(day)} x2={x(day)} y1="264" y2="270" /><text className="chart-label" x={x(day)} y="289" textAnchor="middle">{day === 0 ? "Днес" : shortDate(dateAtOffset(history.as_of, day))}</text></g>)}
        <line className="recovery-cursor-line" x1={x(cursor)} x2={x(cursor)} y1="40" y2="264" />
      </svg>
    </div>
    <div className="recovery-chart-readout" aria-live="polite"><strong>{cursorLabel}</strong><div>{shown.map(row => {
      const value = cursor >= 0 ? forecastReadiness(row.future, cursor) : row.past.flat().find(point => point.day === cursor)?.readiness;
      return <span key={row.zone} style={style(row.zone)}><i aria-hidden="true" />{row.zone} <b>{value === null || value === undefined ? "Няма данни" : `${number.format(value)}%`}</b></span>;
    })}</div></div>
    <label className="recovery-chart-scrubber">Разгледай ден или час<input type="range" min="-120" max="48" step="1" value={Math.round(cursor * 24)} onChange={event => setCursor(snap(Number(event.target.value) / 24))} onKeyDown={event => {
      if (["ArrowLeft", "ArrowDown", "ArrowRight", "ArrowUp"].includes(event.key)) {
        event.preventDefault();
        setCursor(day => stepRecoveryCursor(day, event.key === "ArrowLeft" || event.key === "ArrowDown" ? -1 : 1));
      }
    }} aria-valuetext={cursorLabel} /></label>
    <figcaption><span><i className="recovery-line-key" />История след дневния товар</span><span><i className="recovery-line-key forecast" />Прогноза без нови тренировки</span><span>Праг за готовност: 90%</span></figcaption>
    <p className="recovery-timeline-note">Всички зони използват една и съща времева ос. При срок над 2 дни достигането на 90% е извън показаната прогноза. Историята е по календарни дни; липсващите данни не се приемат за почивка.</p>
  </figure>;
}
