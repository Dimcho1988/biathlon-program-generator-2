"use client";
import { durationHms } from "../lib/duration-format";
import { isRecord } from "../lib/training-status";
import { type PlanProjection, type PlanOutcome } from "../lib/training-management";
const fmt=(x:unknown)=>typeof x==="number" ? `${x<0 ? "−" : ""}${durationHms(Math.abs(x))}` : "—";
export function PlanComparison({plan,outcomes}:{plan?:PlanProjection;outcomes:PlanOutcome[]}) {
  const history=Array.isArray(plan?.history_comparison)?plan.history_comparison.filter(isRecord):[];
  return <section className="management-panel"><h2>Планирано и изпълнено</h2>
    {outcomes.length>0?<><p>Продължителност · ч:мм:сс</p><div className="management-table-wrap"><table><thead><tr><th>Ден</th><th>Планирано</th><th>Изпълнено</th><th>Разлика</th></tr></thead><tbody>{outcomes.map(o=>{const p=o.planned_minutes;const a=o.actual_minutes;return <tr key={o.date}><th>{o.date}</th><td>{fmt(p)}</td><td>{typeof a==="number"?fmt(a):"Непълни данни"}</td><td>{typeof p==="number"&&typeof a==="number"?fmt(a-p):"—"}</td></tr>;})}</tbody></table></div><p className="management-muted">Съпоставяне по календарен ден. Продължителността включва всички реални активности за деня. Липсващи данни остават неизвестни.</p></>:<p>Все още няма отчетени дни от утвърдена програма. Проектът не се представя като изпълнен план.</p>}
    {history.length>0&&<><h3>Реална история · четирите завършени седмици</h3><div className="management-table-wrap"><table><thead><tr><th>Период</th><th>Изпълнено, ч:мм:сс</th><th>Покритие</th></tr></thead><tbody>{history.map((w,i)=><tr key={i}><th>{String(w.start_date)} – {String(w.end_date)}</th><td>{typeof w.actual_minutes==="number"?fmt(w.actual_minutes):"Непълни данни"}</td><td>{String(w.covered_days)} / 7 дни</td></tr>)}</tbody></table></div></>}
  </section>;
}
