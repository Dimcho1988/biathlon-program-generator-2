"use client";
import { useState } from "react";
import { isRecord } from "../lib/training-status";
import { COMPONENTS, type Component, type PlanningDraft, type PlanOutcome } from "../lib/training-management";
const fmt=(x:unknown)=>typeof x==="number"?x.toLocaleString("bg-BG",{maximumFractionDigits:2}):"—";
export function PlanComparison({plan,outcomes}:{plan?:PlanningDraft;outcomes:PlanOutcome[]}) {
  const [measure,setMeasure]=useState<"minutes"|Component>("minutes");
  const history=Array.isArray(plan?.history_comparison)?plan.history_comparison.filter(isRecord):[];
  return <section className="management-panel"><h2>Планирано и изпълнено</h2>
    {outcomes.length>0?<><label>Показател<select value={measure} onChange={e=>setMeasure(e.target.value as typeof measure)}><option value="minutes">Продължителност, минути</option>{COMPONENTS.map(z=><option key={z} value={z}>{z} · приравнени минути</option>)}</select></label><div className="management-table-wrap"><table><thead><tr><th>Ден</th><th>Планирано</th><th>Изпълнено</th><th>Разлика</th></tr></thead><tbody>{outcomes.map(o=>{const p=measure==="minutes"?o.planned_minutes:o.planned_load?.[measure];const a=measure==="minutes"?o.actual_minutes:o.actual_load?.[measure];return <tr key={o.date}><th>{o.date}</th><td>{fmt(p)}</td><td>{typeof a==="number"?fmt(a):"Непълни данни"}</td><td>{typeof p==="number"&&typeof a==="number"?fmt(a-p):"—"}</td></tr>;})}</tbody></table></div><p className="management-muted">Съпоставяне по календарен ден. Приравненият товар включва всички реални активности; не се предполага идентичност на тренировъчния метод. Липсващи данни остават неизвестни.</p></>:<p>Все още няма отчетени дни от утвърдена програма. Проектът не се представя като изпълнен план.</p>}
    {history.length>0&&<><h3>Реална история · четирите завършени седмици</h3><div className="management-table-wrap"><table><thead><tr><th>Период</th><th>Изпълнено, часа</th><th>Покритие</th></tr></thead><tbody>{history.map((w,i)=><tr key={i}><th>{String(w.start_date)} – {String(w.end_date)}</th><td>{typeof w.actual_minutes==="number"?fmt(w.actual_minutes/60):"Непълни данни"}</td><td>{String(w.covered_days)} / 7 дни</td></tr>)}</tbody></table></div></>}
  </section>;
}
