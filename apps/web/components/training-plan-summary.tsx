import Link from "next/link";
import { TimeAvailability, TimeLimitNotice } from "./planning-time-limit";
import { isRecord } from "../lib/training-status";
import { durationHms } from "../lib/duration-format";
import { daySessions, COMPONENTS, type PlanningDraft, type VolumeHistory } from "../lib/training-management";
import { HistoryVolume } from "./planning-controls-editor";
export function TrainingPlanSummary({plan}:{plan:PlanningDraft}) {
  const p=isRecord(plan.parameters)?plan.parameters:{};
  const summary=isRecord(plan.summary)?plan.summary:{};
  const v=isRecord(p.volume_evidence)?p.volume_evidence as unknown as VolumeHistory:null;
  const sports=Array.isArray(p.training_sports)?p.training_sports.map(String):[];
  const sessions=plan.days.flatMap(daySessions);
  const snapshot=isRecord(plan.input_snapshot)?plan.input_snapshot:{};
  const profile=isRecord(snapshot.management_profile)?snapshot.management_profile:{};
  const manual=isRecord(profile.component_targets_weekly)?Object.entries(profile.component_targets_weekly).filter(([,v])=>typeof v==="number"):[];
  const sources=Array.from(new Set(sessions.map(s=>s.dose_evidence.capacity_source)));
  const allocation=isRecord(plan.allocation)?plan.allocation:null;
  const componentAllocation=allocation&&isRecord(allocation.components)?allocation.components:{};
  const allocationRows:Array<Record<string,unknown>&{zone:string}>=COMPONENTS.flatMap(z=>{const row=componentAllocation[z];return isRecord(row)?[{...row,zone:z}]:[];});
  const shortfall=allocationRows.filter(r=>typeof r.unallocated_effective==="number"&&r.unallocated_effective>1).map(r=>r.zone);
  const constraints=allocation&&Array.isArray(allocation.constraints)?allocation.constraints.filter(isRecord):[];
  const limits=allocation&&Array.isArray(allocation.dose_limits)?allocation.dose_limits.map(String):[];
  const limitLabels:Record<string,string>={RECOVERY_RESERVATION_WORK_CAP:"Готовността за предстоящата ключова тренировка или старт",METHOD_CAPACITY_FRACTION:"Делът от индивидуалния капацитет за метода",METHOD_WORK_CAP:"Максималната работа в методния профил",ACTUAL_SPORT_SESSION_EXPOSURE:"Досегашната продължителност на сесиите с конкретното средство",COMPONENT_SLOT_ALLOCATION:"Разпределението на товара между оставащите сесии",ROLLING_7_40_COMPONENT_BUDGET:"Оставащият товар по 7/40",DAILY_AVAILABLE_WORK:"Свободното време за деня",TECHNICAL_SESSION_CEILING:"Максималната продължителност за деня",TAPER_DAILY_WORK_CAP:"Разтоварването преди старт",LOW_ABSOLUTE_RECOVERY_CAP:"Лимитът за възстановителна работа",REMAINING_WEEKLY_WORK:"Седмичният лимит за време",RACE_DURATION_WORK_CAP:"Спецификата на състезателната дисциплина"};
  const duration=(v:unknown)=>typeof v==="number"?durationHms(v):"—";
  const reasons=new Map<string,number>();
  for(const d of plan.days.filter(d=>!d.session)) for(const r of d.rejected_alternatives) reasons.set(r.reason,(reasons.get(r.reason)??0)+1);
  return <section className="management-panel"><h2>Обем и основа на програмата</h2>
    {manual.length>0&&<aside className="management-notice" role="status"><strong>Програмата използва ръчни цели:</strong> {manual.map(([z,v])=>`${z}: ${v} приравнени мин / 7 дни`).join("; ")}.<p>Те заместват автоматичните цели от историята. Нисък бюджет за Z1 може да блокира и по-високите аеробни зони. Ако целта е въведена по погрешка, избери „Използвай автоматичните цели“ в профила и запази.</p><Link href="/planning">Провери ръчните цели →</Link></aside>}
    <div className="management-metrics">
    <div><small>Историческа основа за програмата</small><strong>{duration(p.historical_training_weekly_minutes??p.historical_selected_weekly_minutes??p.baseline_weekly_minutes)}</strong><span>средно за 7 дни</span></div>
    <TimeAvailability context={p}/>
    <div><small>Управление на товара</small><strong>{p.volume_governor==="COMPONENT_7_40"?"7/40 по компоненти":duration(p.weekly_minutes_ceiling)}</strong><span>{p.volume_governor==="COMPONENT_7_40"?"метод + дневна готовност":"ограничение при кратка история"}</span></div>
    <div><small>Предложени тренировки</small><strong>{duration(summary.planned_minutes)}</strong><span>{sessions.length} сесии</span></div>
    </div><TimeLimitNotice context={p}/>{typeof p.available_weekly_minutes === "number" && typeof p.historical_training_weekly_minutes === "number" && Number(p.available_weekly_minutes) < p.historical_training_weekly_minutes && <p className="management-notice">Свободното време в профила е под историческия обем и ограничава седмицата. <Link href="/planning">Провери дните и минутите →</Link></p>}<p className="management-muted">{sources.includes("SPEED_DURATION")?"Използвана е индивидуалната крива скорост–време. ":""}{sources.includes("SPEED_DURATION_PRIOR")?"Използвана е индивидуално мащабирана крива с експертна форма. ":""}{sources.includes("EXPERT_CONTINUOUS_TREF")?"За част от дозите се използва експертен Tref — виж причината в конкретната тренировка. ":""}Наличието на модел не означава, че всяка негова оценка е достатъчно подкрепена за дозиране.</p>
    {allocation&&<>
      {shortfall.length>0&&<aside className="management-notice" role="status"><strong>Остава непланиран товар: {shortfall.join(", ")}.</strong> Програмата покрива част от целите по 7/40. Виж разпределението и ограниченията по-долу; остатъкът не се наваксва автоматично.</aside>}
      <details><summary>Цел и планиран товар по компоненти</summary>
        <p>Прозорец {String(allocation.window_start)} – {String(allocation.window_end)}. Приравнени минути по 7/40, които се отчитат отделно за всяка зона и STR. Те не се събират като обща продължителност.</p>
        <div className="management-table-wrap"><table><thead><tr><th>Компонент</th><th>Цел</th><th>Изпълнено</th><th>Планирано</th><th>Непокрито</th></tr></thead><tbody>{allocationRows.map(row=><tr key={row.zone}><th>{row.zone}</th>{["target_effective","actual_effective","planned_effective","unallocated_effective"].map(k=><td key={k}>{typeof row[k]==="number"?row[k].toLocaleString("bg-BG",{maximumFractionDigits:1}):"—"}</td>)}</tr>)}</tbody></table></div>
        <p>{sessions.length} предложени сесии от {String(allocation.scheduled_slots)} възможни по дните. Седмичен максимум в профила: {String(allocation.weekly_session_limit)}.</p>
        {typeof allocation.scheduled_slots==="number"&&typeof allocation.weekly_session_limit==="number"&&allocation.scheduled_slots<allocation.weekly_session_limit&&<p>Избраните дни и броят сесии в тях разрешават по-малко тренировки от седмичния максимум. <Link href="/planning">Провери „Дни и обем“ →</Link></p>}
        {limits.some(k=>limitLabels[k])&&<><p>При дозите са достигнати следните ограничения:</p><ul>{limits.filter(k=>limitLabels[k]).map(k=><li key={k}>{limitLabels[k]}</li>)}</ul></>}
        {constraints.length>0&&<><p>Условия, които изключват част от методите:</p><ul>{constraints.filter(r=>!["PERIOD_NOT_SUPPORTED","KEY_SLOT_RESERVED","NO_COMPONENT_TARGET"].includes(String(r.code))).slice(0,6).map(r=><li key={String(r.code)}>{String(r.reason)}</li>)}</ul></>}
        <p>Непокритата цел не доказва, че е нужна почивка или че по-пълен план е невъзможен. Това е резултатът при текущите методи, настройки и последователност на избора. Прегледай ограниченията преди промяна на целта.</p>
      </details>
    </>}
    <details><summary>Кои качества тренираме тази седмица?</summary><p>Показана е основната работа. Загрявката, почивките и разливът на товара не се броят като отделна развиваща тренировка.</p><div className="management-zone-legend">{COMPONENTS.map(z=>{const blocks=sessions.flatMap(s=>s.blocks).filter(b=>b.kind==="WORK"&&b.zone===z);const minutes=blocks.reduce((sum,b)=>sum+b.duration_min,0);return <span key={z}>{z}: <strong>{minutes>0?duration(minutes):"без основна работа"}</strong></span>;})}</div><p>Акцентите получават приоритет; останалите качества се поддържат според нуждата и готовността. Не всяка зона изисква отделна тежка тренировка всяка седмица.</p></details>
    <details><summary>Защо обемът е такъв?</summary><HistoryVolume history={v} selected={sports}/><p>Първо се определя целта. Методът, капацитетът, времето, 7/40 и прогнозното възстановяване ограничават конкретната доза. Неизползваният бюджет не се наваксва задължително.</p>{reasons.size>0&&<ul>{[...reasons].sort((a,b)=>b[1]-a[1]).slice(0,5).map(([reason])=><li key={reason}>{reason}</li>)}</ul>}<Link href="/planning">Промени дни, средства, методи и акценти в профила →</Link></details>
  </section>;
}
