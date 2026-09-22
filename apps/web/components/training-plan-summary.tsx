import Link from "next/link";
import { isRecord } from "../lib/training-status";
import { durationHms } from "../lib/duration-format";
import { COMPONENTS, type PlanningDraft, type VolumeHistory } from "../lib/training-management";
import { HistoryVolume } from "./planning-controls-editor";
export function TrainingPlanSummary({plan}:{plan:PlanningDraft}) {
  const p=isRecord(plan.parameters)?plan.parameters:{};
  const summary=isRecord(plan.summary)?plan.summary:{};
  const v=isRecord(p.volume_evidence)?p.volume_evidence as unknown as VolumeHistory:null;
  const sports=Array.isArray(p.training_sports)?p.training_sports.map(String):[];
  const sessions=plan.days.flatMap(d=>d.session?[d.session]:[]);
  const sources=Array.from(new Set(sessions.map(s=>s.dose_evidence.capacity_source)));
  const duration=(v:unknown)=>typeof v==="number"?durationHms(v):"—";
  const reasons=new Map<string,number>();
  for(const d of plan.days.filter(d=>!d.session)) for(const r of d.rejected_alternatives) reasons.set(r.reason,(reasons.get(r.reason)??0)+1);
  return <section className="management-panel"><h2>Обем и основа на програмата</h2><div className="management-metrics">
    <div><small>Историческа основа за програмата</small><strong>{duration(p.historical_training_weekly_minutes??p.historical_selected_weekly_minutes??p.baseline_weekly_minutes)}</strong><span>средно за 7 дни</span></div>
    <div><small>Налично време</small><strong>{p.availability_mode==="AUTO_HISTORY"?"Автоматично":duration(p.available_weekly_minutes)}</strong><span>{p.availability_mode==="AUTO_HISTORY"?"от историята и 7/40":"за 7 дни"}</span></div>
    <div><small>Управление на товара</small><strong>{p.volume_governor==="COMPONENT_7_40"?"7/40 по компоненти":duration(p.weekly_minutes_ceiling)}</strong><span>{p.volume_governor==="COMPONENT_7_40"?"метод + дневна готовност":"ограничение при кратка история"}</span></div>
    <div><small>Предложени тренировки</small><strong>{duration(summary.planned_minutes)}</strong><span>{sessions.length} сесии</span></div>
    </div>{typeof p.available_weekly_minutes === "number" && typeof p.historical_training_weekly_minutes === "number" && Number(p.available_weekly_minutes) < p.historical_training_weekly_minutes && <p className="management-notice">Свободното време в профила е под историческия обем и ограничава седмицата. <Link href="/planning">Провери дните и минутите →</Link></p>}<p className="management-muted">{sources.includes("SPEED_DURATION")?"Използвана е индивидуалната крива скорост–време. ":""}{sources.includes("SPEED_DURATION_PRIOR")?"Използвана е индивидуално мащабирана крива с експертна форма. ":""}{sources.includes("EXPERT_CONTINUOUS_TREF")?"За част от дозите се използва експертен Tref — виж причината в конкретната тренировка. ":""}Наличието на модел не означава, че всяка негова оценка е достатъчно подкрепена за дозиране.</p>
    <details><summary>Кои качества тренираме тази седмица?</summary><p>Показана е основната работа. Загрявката, почивките и разливът на товара не се броят като отделна развиваща тренировка.</p><div className="management-zone-legend">{COMPONENTS.map(z=>{const blocks=sessions.flatMap(s=>s.blocks).filter(b=>b.kind==="WORK"&&b.zone===z);const minutes=blocks.reduce((sum,b)=>sum+b.duration_min,0);return <span key={z}>{z}: <strong>{minutes>0?duration(minutes):"без основна работа"}</strong></span>;})}</div><p>Акцентите получават приоритет; останалите качества се поддържат според нуждата и готовността. Не всяка зона изисква отделна тежка тренировка всяка седмица.</p></details>
    <details><summary>Защо обемът е такъв?</summary><HistoryVolume history={v} selected={sports}/><p>Първо се определя целта. Методът, капацитетът, времето, 7/40 и прогнозното възстановяване ограничават конкретната доза. Неизползваният бюджет не се наваксва задължително.</p>{reasons.size>0&&<ul>{[...reasons].sort((a,b)=>b[1]-a[1]).slice(0,5).map(([reason])=><li key={reason}>{reason}</li>)}</ul>}<Link href="/planning">Промени дни, средства, методи и акценти в профила →</Link></details>
  </section>;
}
