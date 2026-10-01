"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { bandFor, indexNumber, invalidLabel, lineSegments, combinedIndexTrend, type TrainabilityHistory } from "../lib/trainability";
import { TrainabilitySummary } from "./trainability-summary";

const channels = [
  { name: "GENERAL", label: "Общ · 75–92%", color: "var(--accent)" },
  ...[1, 2, 3, 4, 5].map(i => ({ name: `Z${i}`, label: `Z${i}`, color: `var(--zone-${i})` })),
];
const dateLabel = (value: string) => new Date(`${value}T12:00:00Z`).toLocaleDateString("bg-BG", { day: "numeric", month: "short", timeZone: "UTC" });

export function TrainabilityHistoryView({ history }: { history: TrainabilityHistory }) {
  const sports = [...new Set(history.activities.map(a => a.sport))].sort();
  const [sport, setSport] = useState(sports.find(s => history.activities.some(a => a.sport === s && a.index)) ?? sports[0] ?? "");
  const [group, setGroup] = useState("");
  const { groups, selectedGroup, rows } = useMemo(() => {
    const sportRows = history.activities.filter(a => a.sport === sport);
    const groups = [...new Map(sportRows.flatMap(a => a.index ? [[a.index.comparison_key, a.index] as const] : [])).entries()];
    const selectedGroup = groups.some(([key]) => key === group) ? group : groups.at(-1)?.[0];
    const rows = sportRows.filter(a => !a.index || a.index.comparison_key === selectedGroup).sort((a, b) => a.start_at_utc.localeCompare(b.start_at_utc));
    return { groups, selectedGroup, rows };
  }, [history.activities, sport, group]);
  const [enabled, setEnabled] = useState(["GENERAL", "Z1", "Z2", "Z3", "Z4", "Z5"]);
  const [trendDays, setTrendDays] = useState(14);
  const [showTrend, setShowTrend] = useState(true);
  const [selectedRef, setSelectedRef] = useState<string | null>(null);
  const selected = rows.find(a => a.activity_ref === selectedRef) ?? [...rows].reverse().find(a => a.index);
  const selectedPosition = Math.max(0, rows.findIndex(a => a.activity_ref === selected?.activity_ref));
  const trend = useMemo(() => combinedIndexTrend(rows, enabled, trendDays), [rows, enabled, trendDays]);
  const selectedTrend = trend.find(p => p.timestamp === Date.parse(selected?.start_at_utc ?? ""));
  const visible = channels.filter(c => enabled.includes(c.name));
  const values = rows.flatMap(a => visible.flatMap(c => {
    const b = bandFor(a, c.name); return b?.valid && b.index !== null ? [b.index] : [];
  }));
  const min = values.length ? Math.min(...values) : 0;
  const max = values.length ? Math.max(...values) : 1;
  const padding = Math.max((max - min) * 0.12, 0.25);
  const low = Math.max(0, min - padding), high = max + padding;
  const start = Date.parse(`${history.period_start}T00:00:00Z`), end = Date.parse(`${history.period_end}T23:59:59Z`);
  const x = (date: string | number) => 65 + ((typeof date === "number" ? date : Date.parse(date)) - start) / Math.max(86400000, end - start) * 870;
  const y = (value: number) => 260 - (value - low) / (high - low) * 220;
  const trendPath = trend.map((point, i) => {
    if (point.value === null) return "";
    const command = i > 0 && trend[i - 1].value !== null ? "L" : "M";
    return `${command} ${x(point.timestamp)} ${y(point.value)}`;
  }).join(" ");
  const refreshCount = rows.filter(a => a.unavailable_reason === "REFRESH_REQUIRED").length;

  return <div className="trainability-content">
    <section className="index-chart-panel" aria-label="Динамика на индекса">
      <div className="index-filters"><label>Вид спорт<select value={sport} onChange={e => { setSport(e.target.value); setGroup(""); setSelectedRef(null); }}>{sports.map(s => <option key={s}>{s}</option>)}</select></label>
        {groups.length > 1 && <label>Съпоставими настройки<select value={selectedGroup} onChange={e => { setGroup(e.target.value); setSelectedRef(null); }}>{groups.map(([key, index], i) => <option key={key} value={key}>Набор {i + 1} · HRmax {index.hrmax_bpm} · зони {index.zone_bounds_bpm.slice(1, -1).join(" / ")}</option>)}</select></label>}
        <span>{rows.length} активности · всяка точка е една тренировка</span>
      </div>
      <fieldset className="index-channels"><legend>Покажи линии</legend>{channels.map(c => <label key={c.name} style={{ color: c.color }}><input type="checkbox" checked={enabled.includes(c.name)} onChange={() => setEnabled(previous => previous.includes(c.name) ? previous.filter(v => v !== c.name) : [...previous, c.name])} />{c.label}</label>)}</fieldset>
      <div className="index-trend-controls">
        <label><input type="checkbox" checked={showTrend} onChange={e => setShowTrend(e.target.checked)} />Покажи общ среден тренд</label>
        <label>Изглаждане <select value={trendDays} onChange={e => setTrendDays(Number(e.target.value))}>{[7, 14, 28].map(days => <option key={days} value={days}>{days} дни</option>)}</select></label>
        <span>Тънки линии: тренировки · плътна линия: общ среден тренд</span>
      </div>
      {values.length ? <svg viewBox="0 0 1000 310" className="index-history-chart" role="group" aria-label="Индекси по активности. Изберете точка за подробности.">
        {[0, 1, 2, 3, 4].map(tick => { const value = low + (high - low) * tick / 4; return <g key={tick}><line x1="65" x2="935" y1={y(value)} y2={y(value)} /><text x="53" y={y(value) + 4} textAnchor="end">{indexNumber(value, 1)}</text></g>; })}
        {[0, 1, 2, 3, 4].map(tick => { const stamp = start + (end - start) * tick / 4; return <text key={tick} x={65 + tick * 870 / 4} y="288" textAnchor="middle">{dateLabel(new Date(stamp).toISOString().slice(0, 10))}</text>; })}
        {selected && <line className="index-cursor-line" x1={x(selected.start_at_utc)} x2={x(selected.start_at_utc)} y1="35" y2="260" strokeDasharray="4 4" />}
        {showTrend && trendPath.trim() && <path data-trend="combined" d={trendPath} fill="none" stroke="var(--text)" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round" />}
        {visible.map(c => <g key={c.name} style={{ color: c.color }}>
          {lineSegments(rows, c.name).filter(segment => segment.length > 1).map((segment, i) => <polyline key={i} fill="none" stroke="currentColor" opacity={showTrend ? 0.3 : 1} strokeWidth={showTrend ? 1.2 : c.name === "GENERAL" ? 3 : 1.6} points={segment.map(a => `${x(a.start_at_utc)},${y(bandFor(a, c.name)!.index!)}`).join(" ")} />)}
          {rows.map(a => { const band = bandFor(a, c.name); if (!band?.valid || band.index === null) return null; const label = `${dateLabel(a.local_date)} · ${a.name || a.sport} · ${c.label}: ${indexNumber(band.index)}`; return <circle key={a.activity_ref} cx={x(a.start_at_utc)} cy={y(band.index)} r={selected?.activity_ref === a.activity_ref ? 6 : 4} fill="currentColor" tabIndex={0} role="button" aria-label={label} onClick={() => setSelectedRef(a.activity_ref)} onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSelectedRef(a.activity_ref); } }}><title>{label}</title></circle>; })}
        </g>)}
      </svg> : <p className="detail-empty">{enabled.length === 0 ? "Изберете поне една линия." : "Няма валидни индекси за избрания период и спорт. Нужни са активност от поне 7 минути и сумарно време на съпоставените пулс и скорост поне 7 минути за Z1–Z4 и общия диапазон, или 5 минути за Z5."}</p>}
      {rows.length > 0 && selected && <div className="index-scrubber">
        <label htmlFor="index-time-cursor">Проследи във времето <strong>{dateLabel(selected.local_date)} · {selected.name || selected.sport}</strong></label>
        <input id="index-time-cursor" type="range" min="0" max={Math.max(0, rows.length - 1)} step="1" value={selectedPosition} disabled={rows.length < 2} aria-valuetext={`${dateLabel(selected.local_date)} · ${selected.name || selected.sport}`} onChange={e => setSelectedRef(rows[Number(e.target.value)].activity_ref)} />
        <div className="index-cursor-values" aria-live="polite">
          {showTrend && <div className="index-combined-value"><span>Общ среден тренд</span><strong>{indexNumber(selectedTrend?.value ?? null)}</strong><small>{selectedTrend?.count ?? 0} включени точки · {trendDays} дни изглаждане</small></div>}
          {visible.map(c => <div key={c.name} style={{ borderColor: c.color }}><span style={{ color: c.color }}>{c.label}</span><strong>{indexNumber(bandFor(selected, c.name)?.index ?? null)}</strong></div>)}
        </div>
      </div>}
      {showTrend && <p className="index-note">Една обща линия обобщава всички валидни точки от включените индекси. Близките по време точки имат по-голяма тежест; изглаждане {trendDays} дни. Използват се точки от двете страни на датата в избрания период, затова трендът е ретроспективен. Нужни са поне две тренировки. При големи паузи линията прекъсва. Различни спортове и настройки не се смесват. Това е визуално обобщение; то не променя индексите или изчисленията на натоварването.</p>}
      <p className="index-note">Индекс = пулс на общата скала (%HRmax) / Vflat (km/h). За колело към суровия пулс се добавят 7 уд./мин; целевите зони са със 7 удара по-ниски. По-ниската стойност означава по-малък относителен пулс спрямо Vflat. Сравнявайте всяка зона със същата зона. Пулсът е изместен с 20 секунди спрямо Vflat. Цели тренировки отпадат при съмнителен сигнал или отклонение над 20% спрямо достатъчна предходна история. Линиите прекъсват при невалиден индекс; различни спортове и настройки се разглеждат отделно.</p>
      {refreshCount > 0 && <p role="status">{refreshCount} активности са със запазен по-стар анализ. Натиснете „Обнови данните“, за да се добави индексът.</p>}
      {groups.length > 1 && <p className="index-note">Има различни версии на моделите или пулсови настройки. Избраният набор показва само съпоставимите резултати.</p>}
    </section>
    <section className="index-chart-panel"><h2>Стойности по активности</h2><div className="shadow-table-wrap"><table><thead><tr><th>Активност</th>{channels.map(c => <th key={c.name}>{c.label}</th>)}</tr></thead><tbody>{[...rows].reverse().map(a => <tr key={a.activity_ref} className={selected?.activity_ref === a.activity_ref ? "index-selected" : ""}><th scope="row"><button className="index-activity-button" onClick={() => setSelectedRef(a.activity_ref)}>{dateLabel(a.local_date)} · {a.name || a.sport}</button>{a.index?.admission?.status === "EXCLUDED" && <small>{invalidLabel(a.index.admission.reason,420)}</small>}{!a.index && <small>{a.unavailable_reason === "REFRESH_REQUIRED" ? "Нужно е обновяване" : "Липсва HRmod/Vflat анализ"}</small>}</th>{channels.map(c => <td key={c.name}>{indexNumber(bandFor(a, c.name)?.index ?? null)}</td>)}</tr>)}</tbody></table></div>{!rows.length && <p>Няма активности в този период.</p>}</section>
    {selected && <div className="index-selected-detail"><h2>{dateLabel(selected.local_date)} · {selected.name || selected.sport}</h2><Link href={`/activities/${selected.activity_ref}/shadow`}>Отвори HRmod и Vflat анализа →</Link><TrainabilitySummary index={selected.index} /></div>}
  </div>;
}
