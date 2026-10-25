"use client";

import { useId, useState } from "react";
import { durationHms } from "../lib/duration-format";
import { planQComparison } from "../lib/plan-q-comparison";
import { componentLabel } from "../lib/training-visuals";
import type { PlanningDraft } from "../lib/training-management";

const day = (value: string) => `${value.slice(8, 10)}.${value.slice(5, 7)}.${value.slice(0, 4)}`;
const time = (value: number | null) => value === null ? "Няма съпоставима стойност" : durationHms(value);
const COLORS = ["#718399", "#c58a2b", "#24745f"];

export function PlanQComparison({ plan, planKind = "DRAFT" }: { plan: PlanningDraft; planKind?: "DRAFT" | "APPROVED" }) {
  const periods = planQComparison(plan);
  const [selected, setSelected] = useState(0);
  const id = useId();
  if (!periods.length) return null;
  const period = periods[Math.min(selected, periods.length - 1)];
  const keys = ["target", "planned", "actual"] as const;
  const labels = ["Цел Q", planKind === "APPROVED" ? "Утвърдено Q" : "Предложено Q", "Изпълнено Q"];
  const max = Math.max(1, ...period.rows.flatMap(r => keys.map(k => r[k] ?? 0))) * 1.12;
  const top = 18, bottom = 214, left = 78, group = 101, bar = 21;
  const y = (value: number) => bottom - (bottom - top) * value / max;
  return <section className="plan-q-comparison" aria-label="Цел, програма и изпълнение Q">
    <h3>Цел → програма → изпълнение Q</h3>
    {!plan.allocation && <p className="management-notice">Тази версия няма завършен компонентен разчет. Показан е известният Q от съставените сесии; целта и изпълнението остават без съпоставима стойност.</p>}
    {periods.length > 1 && <label>Микроцикъл<select value={Math.min(selected, periods.length - 1)} onChange={e => setSelected(Number(e.target.value))}>{periods.map((p, i) => <option key={`${p.start}-${p.end}`} value={i}>{day(p.start)} – {day(p.end)}</option>)}</select></label>}
    <p>{day(period.start)} – {day(period.end)} · {planKind === "APPROVED" ? "утвърдена програма" : "предложение"}. Q е приравнен обем, показан в ч:мм:сс.</p>
    <div className="management-zone-legend">{labels.map((label, i) => <span key={label}><i style={{ background: COLORS[i] }} aria-hidden="true"/>{label}</span>)}</div>
    <div className="chart-frame" style={{overflowX: "auto"}}><svg viewBox="0 0 710 255" role="img" aria-labelledby={`${id}-title ${id}-description`} style={{ width: "100%", minWidth: 620 }}>
      <title id={`${id}-title`}>Цел, съставена програма и изпълнено Q по компоненти</title>
      <desc id={`${id}-description`}>Три отделни колони за всеки компонент. Точните стойности и липсващите оценки са в таблицата.</desc>
      {[0, 1, 2, 3, 4].map(i => <g key={i}><line x1={left - 10} x2="697" y1={y(max * i / 4)} y2={y(max * i / 4)} stroke="var(--border)"/><text x={left - 16} y={y(max * i / 4) + 4} textAnchor="end" fontSize="11" fill="currentColor">{durationHms(max * i / 4)}</text></g>)}
      {period.rows.map((row, i) => <g key={row.zone}>{keys.map((key, j) => {
        const value = row[key];
        return value === null ? null : <rect key={key} x={left + i * group + j * (bar + 3)} y={y(value)} width={bar} height={bottom - y(value)} fill={COLORS[j]} rx="2"><title>{`${componentLabel(row.zone)} · ${labels[j]}: ${time(value)}`}</title></rect>;
      })}<text x={left + i * group + 35} y="239" textAnchor="middle" fontSize="13" fill="currentColor">{componentLabel(row.zone)}</text></g>)}
    </svg></div>
    <div className="management-table-wrap"><table><caption>Съпоставка Q за {day(period.start)} – {day(period.end)} · ч:мм:сс</caption><thead><tr><th>Компонент</th>{labels.map(label => <th key={label}>{label}</th>)}</tr></thead><tbody>{period.rows.map(row => <tr key={row.zone}><th>{componentLabel(row.zone)}</th>{keys.map(key => <td key={key}>{time(row[key])}</td>)}</tr>)}</tbody></table></div>
    <p className="management-muted">Стойностите са от входните данни при съставянето на тази версия{plan.generated_at ? ` (${day(plan.generated_at.slice(0, 10))})` : ""}. Новото предложение след обновяване на историята има собствен отчет. Изпълненото и съставените сесии се показват отделно.</p>
    {period.includesForecast && <p className="management-muted">Включено е прогнозното продължение след {day(plan.end_date)}, за да се сравни целият микроцикъл.</p>}
    {period.rows.some(r => r.target === null) && <p className="management-muted">Липсващата Q цел не се заменя с нула или с товар E. Целите с разлив E остават в отделната таблица.</p>}
  </section>;
}
