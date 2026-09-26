import { SymptomNotice } from "./symptom-notice";
import { isRecord } from "../lib/training-status";
import { durationHms } from "../lib/duration-format";
import { COMPONENTS, type PlanProjection } from "../lib/training-management";

const n = (v: unknown) => typeof v === "number" && Number.isFinite(v) ? v.toLocaleString("bg-BG", {maximumFractionDigits:2}) : "—";
const time = (v: unknown) => typeof v === "number" && Number.isFinite(v) ? durationHms(v) : "—";
const reasons: Record<string,string> = {
  NO_RELIABLE_COMPONENT_HISTORY:"Липсва надеждна компонентна история; опората не разрешава нова доза.",
  NO_OBSERVED_EXPOSURE:"Няма измерено натоварване; въвеждането на компонента изисква треньорска преценка.",
  BELOW_REFERENCE_BOUND:"Плановата основа е долната граница. Конкретната доза се проверява отделно спрямо актуалната история и готовност.",
  ABOVE_REFERENCE_BOUND:"Плановата основа е горната граница. Измереното изпълнение остава запазено без промяна.",
};
export function LoadProgressionSummary({plan}: {plan?: PlanProjection}) {
  const outlook = isRecord(plan?.long_term) ? plan.long_term : {};
  const model = isRecord(outlook.progression) ? outlook.progression : null;
  const history = Array.isArray(plan?.component_history) ? plan.component_history.filter(isRecord) : [];
  if (!model) return null;
  const components = isRecord(model.components) ? model.components : {};
  const feedback = isRecord(model.adaptation) ? model.adaptation : {};
  const outcomes = Array.isArray(feedback.evidence) ? feedback.evidence.filter(isRecord) : [];
  const anchor = isRecord(model.anchor) ? model.anchor : {};
  const windows = Array.isArray(anchor.windows) ? anchor.windows.filter(isRecord) : [];
  const stable = model.basis === "STABLE_PREPARATION_REFERENCE";
  return <section className="management-panel" aria-label="Прираст и изпълнен товар">
    <details><summary>Опорна база и прираст</summary>
    {stable ? <>
      <p>Устойчива база от {String(anchor.created_on ?? "—")}. Цел до {String(model.target_date ?? "края на подготовката")}. Смяната на акцента и разтоварването не зануляват базата.</p>
      <p className="management-muted">Всички времена тук са приравнен обем за 7 дни, в ч:мм:сс, а не продължителност на тренировките. Избраната основа е измерената история, ограничена между експертните граници. От нея се изчисляват и прирастът, и седмичните цели.</p>
      <div style={{overflowX:"auto"}}><table><thead><tr><th>Компонент</th><th>Историческа основа</th><th>Експертни граници</th><th>Избрана планова основа</th><th>Темп, %/год.</th><th>Планиран прираст до датата, %</th><th>Средна седмична цел до датата</th></tr></thead><tbody>{COMPONENTS.map(z=>{
        const c=isRecord(components[z])?components[z]:{};
        return <tr key={z}><th>{z}</th><td>{time(c.weekly_q)}</td><td>{Array.isArray(c.expert_q_bounds) ? c.expert_q_bounds.map(time).join(" – ") : "—"}</td><td>{time(c.reference_q)}</td><td>{n(c.governed_annual_rate_percent ?? c.annual_rate_percent)}</td><td>{n(c.planned_growth_percent)}</td><td>{time(c.target_q)}</td></tr>;
      })}</tbody></table></div>
      <p>Годишният темп включва оценената обратна връзка и се натрупва според календарното време. В общата подготовка се развиват всички аеробни компоненти; след това прирастът следва специалните приоритети и настройките по периоди. Отделната колона показва какъв прираст действително е заложен до посочената дата.</p>
      <p>Средната цел се разпределя между натоварващите и възстановителните микроцикли. Дневните ограничения могат да намалят съставените тренировки. Това е планова цел, не обещан резултат; няма автоматично наваксване.</p>
      {model.history_usable===false&&<p role="status">Текущата история изисква проверка. Запазената опора не замества липсващите измервания или готовност.</p>}
      {COMPONENTS.map(z=>{const c=isRecord(components[z])?components[z]:{};return reasons[String(c.limitation)] ? <p key={z}><strong>{z}:</strong> {reasons[String(c.limitation)]}</p> : null;})}
      <details><summary>Произход на базата и приравняване</summary>
        <p>{model.anchor_reused===true ? "Използвана е запазената опорна база." : "Новата опора се запазва с генерирането на проект."} Измерената база е медиана на наличните цели мезоцикли, включително разтоварването; при липса — цял 40-дневен прозорец.</p>
        {windows.map(w=><p key={String(w.start_date)}>{String(w.start_date)} – {String(w.end_date)} · {n(w.covered_days)} дни</p>)}
        <p>Експертната позиция според нивото и общия обем се използва само при липсваща надеждна история. При налична история позицията не я замества. За силата няма автоматична експертна граница или годишен прираст.</p>
        <p>Z1–Z4: 3% за удар спрямо горната граница. Z5: 5% за удар над долната граница, до HRmax. Пример за Z5 180–200: 5 реални минути при 200 = 10 приравнени минути при 180. Треньорско правило, не доказана физиологична взаимозаменяемост.</p>
      </details>

    </> : <p>База: {model.basis === "COMPLETED_CYCLE" ? "завършен мезоцикъл с разтоварването" : "последните 40 дни"}. Архивен отчет по предишната версия на модела.</p>}
    <details><summary>Реална промяна между завършени мезоцикли</summary>
      <p>Отрицателният процент тук описва измереното изпълнение; не е предписание за намаляване на следващия обем.</p>
      <table><thead><tr><th>Компонент</th><th>Измерена промяна в приравнения обем, %</th></tr></thead><tbody>{COMPONENTS.map(z=>{
        const c=isRecord(components[z])?components[z]:{},g=isRecord(c.observed_cycle_growth_percent)?c.observed_cycle_growth_percent:{};
        return <tr key={z}><th>{z}</th><td>{n(g.weekly_q)}</td></tr>;
      })}</tbody></table>
    </details>
    {feedback.hold_for_reported_illness_or_pain===true&&<SymptomNotice day={feedback.latest_report_day}/>}
    <p>Обратна връзка: {outcomes.filter(e=>e.status==="POSITIVE"||e.status==="NEGATIVE").length} оценени реакции след завършен блок. Потвърдената положителна реакция може да увеличи бъдещия темп по компонента; отрицателната го намалява. Липсата на оценка оставя началния темп. Recovery се използва отделно за разпределянето на тренировките.</p>
    <details><summary>История по седмици: продължителност и приравнен обем</summary><div style={{overflowX:"auto"}}><table><thead><tr><th>Период</th><th>Компонент</th><th>Продължителност в зоната</th><th>Приравнен обем</th></tr></thead><tbody>{history.flatMap(w=>COMPONENTS.map(z=>{
      const cs=isRecord(w.components)?w.components:{}; const c=isRecord(cs[z])?cs[z]:{};
      return <tr key={String(w.start_date)+z}><td>{String(w.start_date)} – {String(w.end_date)}</td><th>{z}</th><td>{time(c.weekly_minutes)}</td><td>{time(c.weekly_q)}</td></tr>;
    }))}</tbody></table></div></details>
    </details>
  </section>;
}
