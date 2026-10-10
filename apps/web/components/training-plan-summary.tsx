import Link from "next/link";
import { TimeAvailability, TimeLimitNotice } from "./planning-time-limit";
import { isRecord } from "../lib/training-status";
import { durationHms } from "../lib/duration-format";
import { componentColor, componentLabel } from "../lib/training-visuals";
import { daySessions, rejectedMethodIds, COMPONENTS, type PlanningDraft, type VolumeHistory } from "../lib/training-management";
import { HistoryVolume } from "./planning-controls-editor";
import { PlanningEvidenceNotice, planningHistoryEstimated } from "./planning-evidence-notice";

type AllocationRow = Record<string, unknown> & { zone: string };
const allocationRows = (components: unknown): AllocationRow[] => !isRecord(components) ? [] : COMPONENTS.flatMap(zone => {
  const row = components[zone];
  return isRecord(row) ? [{ ...row, zone }] : [];
});
const remainder = (row: Record<string, unknown>) => "remaining" in row ? row.remaining : row.unallocated_effective;
const duration = (value: unknown) => typeof value === "number" ? durationHms(value) : "—";
const dateLabel = (value: unknown) => typeof value === "string" ? new Date(`${value}T12:00:00Z`).toLocaleDateString("bg-BG", { timeZone: "UTC" }) : "—";

function AllocationCoverage({ components, caption, estimatedHistory, segment = false }: {
  components: unknown; caption: string; estimatedHistory: boolean; segment?: boolean;
}) {
  const rows = allocationRows(components);
  if (!rows.length) return null;
  return <div className="management-table-wrap"><table>
    <caption>{caption} · ч:мм:сс</caption>
    <thead><tr><th>Компонент / величина</th><th>Желана цел</th><th>{segment ? "Цел за отрязъка" : "Съгласувана цел"}</th><th>{estimatedHistory ? "Изпълнено · с оценен товар" : "Изпълнено"}</th><th>Планирано</th><th>Остатък</th><th>Покритие</th>{segment && <th>Оставащ товар с разлив</th>}</tr></thead>
    <tbody>{rows.map(row => {
      const target = row.target ?? row.target_effective;
      const actual = "actual" in row ? row.actual : row.actual_effective;
      const planned = row.planned ?? row.planned_effective;
      const percent = typeof target === "number" && target > 0 && typeof actual === "number" && typeof planned === "number" ? Math.min(100, 100 * (actual + planned) / target) : null;
      return <tr key={row.zone}><th><span className="management-zone-legend"><i style={{ background: componentColor(row.zone) }} aria-hidden="true"/>{componentLabel(row.zone)}</span><small>{row.basis === "DIRECT_Q" ? "Приравнен обем" : "Товар с разлив"}</small>{(row.actual_exceeds_target === true || row.actual_effective_exceeds_target === true) && <small>Изпълненото е над целта</small>}</th><td>{duration(row.desired_target_q ?? target)}</td><td>{duration(target)}</td><td>{duration(actual)}</td><td>{duration(planned)}</td><td>{duration(remainder(row))}</td><td>{percent === null ? "—" : `${percent.toFixed(1)}%`}</td>{segment && <td>{duration(row.remaining_effective ?? row.unallocated_effective)}</td>}</tr>;
    })}</tbody>
  </table></div>;
}

export function TrainingPlanSummary({plan}:{plan:PlanningDraft}) {
  const p=isRecord(plan.parameters)?plan.parameters:{};
  const summary=isRecord(plan.summary)?plan.summary:{};
  const volumes=Array.isArray(summary.volume_by_microcycle)?summary.volume_by_microcycle.filter(isRecord):[];
  const v=isRecord(p.volume_evidence)?p.volume_evidence as unknown as VolumeHistory:null;
  const sports=Array.isArray(p.training_sports)?p.training_sports.map(String):[];
  const sessions=plan.days.flatMap(daySessions);
  const estimatedHistory=planningHistoryEstimated(plan);
  const snapshot=isRecord(plan.input_snapshot)?plan.input_snapshot:{};
  const profile=isRecord(snapshot.management_profile)?snapshot.management_profile:{};
  const manual=isRecord(profile.component_targets_weekly)?Object.entries(profile.component_targets_weekly).filter(([,v])=>typeof v==="number"):[];
  const sources=Array.from(new Set(sessions.map(s=>s.dose_evidence.capacity_source)));
  const allocation=isRecord(plan.allocation)?plan.allocation:null;
  const segments=allocation&&Array.isArray(allocation.segments)?allocation.segments.filter(segment=>isRecord(segment)&&isRecord(segment.components)):[];
  const rows=segments.length?segments.flatMap(segment=>allocationRows(segment.components)):allocationRows(allocation?.components);
  const shortfall=Array.from(new Set(rows.filter(r=>remainder(r)===null||typeof remainder(r)==="number"&&Number(remainder(r))>1).map(r=>r.zone)));
  const constraints=allocation&&Array.isArray(allocation.constraints)?allocation.constraints.filter(isRecord):[];
  const limits=allocation&&Array.isArray(allocation.dose_limits)?allocation.dose_limits.map(String):[];
  const limitLabels:Record<string,string>={COMPLETE_DOSE_ALLOCATION:"Разпределение в цели сесии над минималната доза",ROLLING_Q_AND_7_40_BUDGET:"Оставащият обем и товар от дългосрочната цел",FUTURE_QUALITY_RESERVATION_WORK_CAP: "Резерв за бъдеща основна или силова тренировка", RECOVERY_RESERVATION_WORK_CAP:"Готовността за предстоящата ключова тренировка или старт",METHOD_CAPACITY_FRACTION:"Делът от индивидуалния капацитет за метода",METHOD_WORK_CAP:"Максималната работа в методния профил",ACTUAL_SPORT_SESSION_EXPOSURE:"Досегашната продължителност на сесиите с конкретното средство",COMPONENT_SLOT_ALLOCATION:"Разпределението на товара между оставащите сесии",ROLLING_7_40_COMPONENT_BUDGET:"Оставащият товар за текущия микроцикъл",DAILY_AVAILABLE_WORK:"Свободното време за деня",TECHNICAL_SESSION_CEILING:"Максималната продължителност за деня",TAPER_DAILY_WORK_CAP:"Разтоварването преди старт",LOW_ABSOLUTE_RECOVERY_CAP:"Лимитът за възстановителна работа",REMAINING_WEEKLY_WORK:"Седмичният лимит за време",RACE_DURATION_WORK_CAP:"Спецификата на състезателната дисциплина"};
  const reasons=new Map<string,number>();
  for(const d of plan.days.filter(d=>!d.session)) for(const r of d.rejected_alternatives) reasons.set(r.reason,(reasons.get(r.reason)??0)+rejectedMethodIds(r).length);
  return <section className="management-panel"><h2>Обем и основа на програмата</h2>
    <PlanningEvidenceNotice plan={plan}/>
    {manual.length>0&&<aside className="management-notice" role="status"><strong>Програмата използва ръчни цели:</strong> {manual.map(([z,v])=>`${z}: ${duration(v)} приравнено време / 7 дни`).join("; ")}.<p>Те заместват автоматичните цели от историята. Нисък бюджет за Z1 може да блокира и по-високите аеробни зони. Ако целта е въведена по погрешка, избери „Използвай автоматичните цели“ в профила и запази.</p><Link href="/planning">Провери ръчните цели →</Link></aside>}
    <div className="management-metrics">
    <div><small>Средна продължителност от историята</small><strong>{duration(p.historical_training_weekly_minutes??p.historical_selected_weekly_minutes??p.baseline_weekly_minutes)}</strong><span>време за тренировки за 7 дни</span></div>
    <TimeAvailability context={p}/>
    <div><small>Управление на товара</small><strong>{p.volume_governor==="COMPONENT_7_40"?(segments.length?"Цели на дългосрочната програма":"7/40 по компоненти"):duration(p.weekly_minutes_ceiling)}</strong><span>{p.volume_governor==="COMPONENT_7_40"?"метод + дневна готовност":"ограничение при кратка история"}</span></div>
    <div><small>Продължителност на предложените тренировки</small><strong>{duration(summary.planned_minutes)}</strong><span>{sessions.length} сесии · {dateLabel(plan.start_date)} – {dateLabel(plan.end_date)}</span></div>
    {volumes.map(volume=><div key={String(volume.start_date)}><small>Изпълнено + предложено · {dateLabel(volume.start_date)} – {dateLabel(volume.end_date)}</small><strong>{duration(volume.total_minutes)}</strong><span>{duration(volume.actual_minutes)} изпълнено + {duration(volume.planned_minutes)} предложено{volume.complete_microcycle!==true&&` · до ${dateLabel(volume.through_date)}, непълен микроцикъл`}</span></div>)}
    </div><TimeLimitNotice context={p}/>{typeof p.available_weekly_minutes === "number" && typeof p.historical_training_weekly_minutes === "number" && Number(p.available_weekly_minutes) < p.historical_training_weekly_minutes && <p className="management-notice">Свободното време в профила е под историческия обем и ограничава седмицата. <Link href="/planning">Провери дните и минутите →</Link></p>}<p className="management-muted">{sources.includes("BLENDED_DOSING_CURVE")?"Дозировката използва общата крива: 30% от индекса и 70% от максималните тестове. ":""}{sources.includes("SPEED_DURATION")?"Използвана е индивидуалната крива скорост–време. ":""}{sources.includes("SPEED_DURATION_PRIOR")?"Използвана е индивидуално мащабирана крива с експертна форма. ":""}{sources.some(source => ["EXPERT_CONTINUOUS_TREF", "EXPERT_CONTINUOUS_TMAX", "EXPERT_TMAX"].includes(source))?"За част от дозите се използва експертен Tmax — виж причината в конкретната тренировка. ":""}Наличието на модел не означава, че всяка негова оценка е достатъчно подкрепена за дозиране.</p>
    {allocation&&<>
      {shortfall.length>0&&<aside className="management-notice" role="status"><strong>Остава непланиран товар: {shortfall.join(", ")}.</strong> Целта за показаните дати не е покрита изцяло. Остатъкът остава видим за преглед на ограниченията и разпределението.</aside>}
      {segments.length ? <>{segments.map(segment=><AllocationCoverage key={`${segment.window_start}-${segment.window_end}`} components={segment.components} caption={`Дългосрочна цел · ${dateLabel(segment.window_start)} – ${dateLabel(segment.window_end)}`} estimatedHistory={estimatedHistory} segment/>)}</> : <AllocationCoverage components={allocation.components} caption="Покритие на целите за периода" estimatedHistory={estimatedHistory}/>}
      {rows.length>0&&<p className="management-muted">Изпълненото и планираното са за точните показани дати. {segments.length?"Всеки отрязък от микроцикъл запазва собствената си дългосрочна цел; непълната седмица получава съответния дял. Остатъкът не се прехвърля между микроцикли. Прекият приравнен обем и оставащият товар с разлив се проверяват отделно. ":"Желаната цел следва дългосрочната динамика. "}Дневната готовност определя изпълнимите задачи. 7/40 и възстановяването се преизчисляват от целия действителен и планиран товар. Непокритата цел остава видима. Ръчните цели за товар се показват в собствената им величина.</p>}
      <details><summary>Цел и планиран товар по компоненти</summary>
        <p>Приравнен обем от предложените сесии · ч:мм:сс. Отчита интензивността в зоната и се различава от продължителността на тренировките, показана по-горе.</p>
        <div className="management-zone-legend">{COMPONENTS.map(z => {
          const values = sessions.map(s => s.direct_equivalent_minutes?.[z]);
          const known = values.every(v => typeof v === "number" && Number.isFinite(v));
          return <span key={z}><i style={{background:componentColor(z)}} aria-hidden="true"/>{componentLabel(z)}: <strong>{known ? durationHms(values.reduce<number>((sum, v) => sum + (v ?? 0), 0)) : "—"}</strong></span>;
        })}</div>
        <p>{sessions.length} предложени сесии от {String(allocation.scheduled_slots)} възможни по дните. Седмичен максимум в профила: {String(allocation.weekly_session_limit)}.</p>
        {typeof allocation.scheduled_slots==="number"&&typeof allocation.weekly_session_limit==="number"&&allocation.scheduled_slots<allocation.weekly_session_limit&&<p>Избраните дни и броят сесии в тях разрешават по-малко тренировки от седмичния максимум. <Link href="/planning">Провери „Дни и обем“ →</Link></p>}
        {limits.some(k=>limitLabels[k])&&<><p>При дозите са достигнати следните ограничения:</p><ul>{limits.filter(k=>limitLabels[k]).map(k=><li key={k}>{limitLabels[k]}</li>)}</ul></>}
        {constraints.length>0&&<><p>Условия, които изключват част от методите:</p><ul>{constraints.filter(r=>!["PERIOD_NOT_SUPPORTED","KEY_SLOT_RESERVED","NO_COMPONENT_TARGET"].includes(String(r.code))).slice(0,6).map(r=><li key={String(r.code)}>{String(r.reason)}</li>)}</ul></>}
        <p>Непокритата цел не доказва, че е нужна почивка или че по-пълен план е невъзможен. Това е резултатът при текущите методи, настройки и последователност на избора. Прегледай ограниченията преди промяна на целта.</p>
      </details>
    </>}
    <details><summary>Кои качества тренираме тази седмица?</summary><p>Показана е основната работа. Загрявката, почивките и разливът на товара не се броят като отделна развиваща тренировка.</p><div className="management-zone-legend">{COMPONENTS.map(z=>{const blocks=sessions.flatMap(s=>s.blocks).filter(b=>b.kind==="WORK"&&b.zone===z);const minutes=blocks.reduce((sum,b)=>sum+b.duration_min,0);return <span key={z}><i style={{background:componentColor(z)}} aria-hidden="true"/>{componentLabel(z)}: <strong>{minutes>0?duration(minutes):"без основна работа"}</strong></span>;})}</div><p>Акцентите получават приоритет; останалите качества се поддържат според нуждата и готовността. Не всяка зона изисква отделна тежка тренировка всяка седмица.</p></details>
    <details><summary>Защо обемът е такъв?</summary><HistoryVolume history={v} selected={sports}/><p>Дългосрочната цел направлява плана. Методът, Tmax, времето и прогнозното възстановяване определят конкретната доза. Общият товар включва загрявката, почивките и разлива; от него се изчисляват 7/40 и Recovery. Непокритият обем остава видим за преглед.</p>{reasons.size>0&&<ul>{[...reasons].sort((a,b)=>b[1]-a[1]).slice(0,5).map(([reason])=><li key={reason}>{reason}</li>)}</ul>}<Link href="/planning">Промени дни, средства, методи и акценти в профила →</Link></details>
  </section>;
}
