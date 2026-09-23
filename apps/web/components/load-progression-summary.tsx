import { isRecord } from "../lib/training-status";
import { COMPONENTS, type PlanProjection } from "../lib/training-management";

const n = (v: unknown) => typeof v === "number" && Number.isFinite(v) ? v.toLocaleString("bg-BG", {maximumFractionDigits:2}) : "—";
export function LoadProgressionSummary({plan}: {plan?: PlanProjection}) {
  const outlook = isRecord(plan?.long_term) ? plan.long_term : {};
  const model = isRecord(outlook.progression) ? outlook.progression : null;
  const history = Array.isArray(plan?.component_history) ? plan.component_history.filter(isRecord) : [];
  if (!model) return null;
  const components = isRecord(model.components) ? model.components : {};
  const feedback = isRecord(model.adaptation) ? model.adaptation : {};
  const outcomes = Array.isArray(feedback.evidence) ? feedback.evidence.filter(isRecord) : [];
  return <section className="management-panel" aria-label="Прираст и изпълнен товар">
    <h2>Прираст и изпълнен товар</h2>
    <p>База: {model.basis === "COMPLETED_CYCLE" ? "завършен мезоцикъл с разтоварването" : "последните 40 дни"}. Индексът 7/40 показва текущото натоварване спрямо историята; промяната в обема се отчита отделно.</p>
    <div style={{overflowX:"auto"}}><table><thead><tr><th>Компонент</th><th>База Q, мин/7 дни</th><th>Годишна настройка, %</th><th>Реална промяна Q, %</th><th>Реална промяна E, %</th></tr></thead><tbody>{COMPONENTS.map(z=>{
      const c=isRecord(components[z])?components[z]:{}; const g=isRecord(c.observed_cycle_growth_percent)?c.observed_cycle_growth_percent:{};
      return <tr key={z}><th>{z}</th><td>{n(c.weekly_q)}</td><td>{n(c.annual_rate_percent)}</td><td>{n(g.weekly_q)}</td><td>{n(g.weekly_effective)}</td></tr>;
    })}</tbody></table></div>
    <p className="management-muted">Реалната промяна сравнява два цели завършени мезоцикъла. Q е приравненото време преди преливането; E е ефективният товар след него. Липсващите данни са „—“. Процентите са начална треньорска настройка и не обещават достижим прираст.</p>
    {feedback.hold_for_reported_illness_or_pain===true&&<p className="management-notice">Последният отчет съдържа сигнал за болка или заболяване: програмата изисква преглед.</p>}
    <p>Обратна връзка: {outcomes.filter(e=>e.status==="POSITIVE"||e.status==="NEGATIVE").length} оценени реакции след завършен блок. Recovery се използва отделно за разпределянето на тренировките.</p>
    <details><summary>История по седмици: време, Q, E и 7/40</summary><div style={{overflowX:"auto"}}><table><thead><tr><th>Период</th><th>Компонент</th><th>Реални минути</th><th>Q</th><th>E</th><th>7/40</th></tr></thead><tbody>{history.flatMap(w=>COMPONENTS.map(z=>{
      const cs=isRecord(w.components)?w.components:{}; const c=isRecord(cs[z])?cs[z]:{};
      return <tr key={String(w.start_date)+z}><td>{String(w.start_date)} – {String(w.end_date)}</td><th>{z}</th><td>{n(c.weekly_minutes)}</td><td>{n(c.weekly_q)}</td><td>{n(c.weekly_effective)}</td><td>{n(c.index_7_40)}</td></tr>;
    }))}</tbody></table></div></details>
  </section>;
}
