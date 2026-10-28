"use client";

import { useState } from "react";
import { durationHms } from "../lib/duration-format";
import { planQComparison, qPercent, type QComparisonOptions } from "../lib/plan-q-comparison";
import { componentLabel, componentColor } from "../lib/training-visuals";
import type { PlanningDraft } from "../lib/training-management";
import { availablePlanWeeks, planDateLabel as day, planRangeLabel, selectedPlanWeek } from "../lib/plan-week-view";

const time = (value: number | null) => value === null ? "Няма съпоставима стойност" : durationHms(value);
const percent = (value: number | null) => value === null ? "—" : `${value.toFixed(1)}%`;

export function PlanQComparison({plan, planKind = "DRAFT", ...options}: {plan: PlanningDraft} & QComparisonOptions) {
  const periods = planQComparison(plan, {...options, planKind});
  const [selected, setSelected] = useState<string | null>(null);
  if (!periods.length) return null;
  const today = options.today ?? periods[0].actualAsOf;
  const weeks = availablePlanWeeks(plan,today);
  const week = selectedPlanWeek(plan,today,options.selectedWeek ?? selected);
  const period = periods.find(p => p.start === week.start)!;
  const plannedLabel = `${planKind === "APPROVED" ? "утвърдено" : "предложение"}${period.includesForecast ? " + прогноза" : ""}`;
  const strength = plan.allocation?.strength_feasibility?.segments?.find(s => s.window_start === period.start && s.window_end === period.end);
  return <section className="plan-q-comparison" aria-label="Цел, програма и изпълнение Q">
    <h3>Цел → планирано → изпълнено</h3>
    {weeks.length > 1 && <label>Седмица<select value={period.start} onChange={e => {setSelected(e.target.value); options.onWeekChange?.(e.target.value);}}>{weeks.map((p,i) => <option key={p.start} value={p.start}>{i > 0 ? "Следваща седмица" : p.end < today ? "Запазена седмица" : p.start > today ? "Предстояща седмица" : "Текуща седмица"} · {planRangeLabel(p)}</option>)}</select></label>}
    <p><strong>{planRangeLabel(period)}</strong> · {planKind === "APPROVED" ? "утвърдена програма" : "предложение"}{period.includesForecast ? " с прогнозно продължение" : ""}.</p>
    {period.includesForecast && <p className="management-notice">Прогнозни дни: {day(period.forecastDates[0])} – {day(period.forecastDates[period.forecastDates.length-1])}. Задачите им са включени в „Планирано“ и в графика по-долу, с обозначение „Прогноза“. Те се преразглеждат при обновяване и още не са утвърдени.</p>}
    {period.missingDates.length > 0 && <p className="management-notice">Непълен разчет: липсват задачи за {period.missingDates.map(day).join(", ")}. Общото планирано Q остава неизвестно до обновяване.</p>}
    <p>Приравнен обем Q в ч:мм:сс. Целта от дългосрочната програма е 100% за целия избран микроцикъл.</p>
    <div className="management-table-wrap"><table className="q-primary-table"><caption>Q по компоненти · {day(period.start)} – {day(period.end)}</caption>
      <thead><tr><th>Компонент</th><th>Цел по модела</th><th>Планирано · {plannedLabel}</th><th>Изпълнено</th></tr></thead>
      <tbody>{period.rows.map(row => <tr key={row.zone}>
        <th><span className="management-zone-legend"><i style={{background: componentColor(row.zone)}} aria-hidden="true"/>{componentLabel(row.zone)}</span></th>
        <td data-label="Цел">{time(row.target)}<small>{row.target !== null && row.target > 0 ? "100%" : row.target === 0 ? "Няма зададен обем" : "Целта по Q е неизвестна"}</small></td>
        <td data-label="Планирано">{time(row.planned)}<small>{percent(qPercent(row.planned, row.target))} от целта</small>{row.forecast !== null && row.forecast > 0 && <small>Вкл. прогноза: {time(row.forecast)}</small>}</td>
        <td data-label="Изпълнено">{time(row.actual)}<small>{percent(qPercent(row.actual, row.target))} от целта</small>{row.actual !== null && row.actualEstimated !== null && row.actualEstimated > 0 && <small>От него оценено: {time(row.actualEstimated)}</small>}</td>
      </tr>)}</tbody></table></div>
    <p className="management-muted">График и цел: {planRangeLabel(period)}. {period.liveExecution ? "Изпълнението е обновено от реалните записи" : "Архивна оценка на изпълнението при съставяне на плана"} към {day(period.actualAsOf)}. Текущият ден може да е непълен.</p>
    {period.rows.some(r => r.actualEstimated !== null && r.actualEstimated > 0) && <p className="management-muted">Оцененото Q допълва участъци с липсващ сигнал в реално записани активности по същия метод като модела за планиране. То е част от показаното изпълнение, но не е измерено разпределение по зони.</p>}
    <details><summary>Как да разчитам отчета</summary>
      <p>„Планирано“ съдържа задачите за целия избран микроцикъл, включително обозначената прогноза. При програма, съставена след реални тренировки, това може да са само оставащите задачи. Изпълненото се отчита отделно.</p>
      <p>Целим 100%, с допустими 90–110% за всеки компонент в разпределението на целия микроцикъл. Процентът на изпълнение показва напредък; ниска стойност в незавършен цикъл сама по себе си не означава изоставане.</p>
    </details>
    {strength?.quota_bound_below_target && <p className="management-notice">При зададения лимит от {strength.session_quota} силови сесии изчислената горна граница е {percent(qPercent(strength.attainable_upper_bound_q, strength.desired_q))} от силовата цел. Ограничението и методните дози са показани в подробния разчет.</p>}
    {planKind === "APPROVED" && <details><summary>Спазване на плана до завършилите дни</summary>
      <p>Сравняват се само отчетените завършили дни с предписания за същите дати. Текущият ден и бъдещите задачи не се оценяват като пропуснати. Съпоставянето е по календарен ден, без доказване на изпълнение на конкретни блокове.</p>
      <table><thead><tr><th>Компонент</th><th>Планирано до момента</th><th>Изпълнено за същите дни</th><th>Спрямо планираното</th></tr></thead><tbody>{period.rows.map(r => <tr key={r.zone}><th>{componentLabel(r.zone)}</th><td>{time(r.plannedCompleted)}</td><td>{time(r.actualCompleted)}</td><td>{percent(qPercent(r.actualCompleted, r.plannedCompleted))}</td></tr>)}</tbody></table>
    </details>}
    {period.rows.some(r => r.target === null || r.planned === null || r.actual === null) && <p className="management-muted">Липсващите Q стойности остават неизвестни. Продължителността и товарът E не ги заместват; старите предписания без запазено Q не се преизчисляват със задна дата.</p>}
  </section>;
}
