import { isRawMovingSpeed, type SpeedRole } from "../lib/speed-basis";
import { durationSeconds } from "../lib/duration-format";
import Link from "next/link";
import type {HrModel,SpeedModel} from "../lib/models";

const n=(v:number)=>new Intl.NumberFormat("bg-BG",{maximumFractionDigits:2}).format(v);
const duration = (value:number|null) => value === null ? "Извън обхвата на кривата" : durationSeconds(value);
type Zone=HrModel["zones"][number];
const sourceLabel=(source:string)=>source==="INITIAL_HEURISTIC_V50_REFERENCE_SCALE"?"Начална оценка · V50 и нормативна форма":source==="INITIAL_V50_V6_ESTIMATE"?"Начална оценка по V50/V6":source.includes("GENERAL")?"Общ наблюдаван ТИ":source==="INDEX"?"Приет ТИ":source==="EXPERT_HISTORY"?"Експертен ориентир от историята":source==="EXPERT_MINIMUM"?"Експертен минимум":"Среда на експертния диапазон";
function candidateExplanation(z:Zone){
  if(z.source==="INITIAL_HEURISTIC_V50_REFERENCE_SCALE")return "Границата е моделна оценка от прогнозната V50 и нормативната форма; няма индивидуално измерване.";
  switch(z.candidate_reason){
    case "BOUNDARY_PRESERVED":return z.duration_s === null ? "Скоростната граница е запазена. Времето е извън обхвата на кривата." : "Скоростната граница е запазена; времето е оценено по избраната крива.";
    case "ACCEPTED":return "В допустимия диапазон.";
    case "NO_VALID_INDEX":return "Няма валиден зонален ТИ от допуснатите тренировки.";
    case "SPEED_BELOW_CURVE":return "Скоростта от ТИ е под минималната скорост на кривата; време не се изчислява извън обхвата ѝ.";
    case "SPEED_ABOVE_CURVE":return "Скоростта от ТИ е над максималната скорост на кривата; време не се изчислява извън обхвата ѝ.";
    case "DURATION_BELOW_MIN":return `Времето от ТИ е под ${duration(z.duration_min_s)}.`;
    case "DURATION_ABOVE_MAX":return `Времето от ТИ е над ${duration(z.duration_max_s)}.`;
    default:return z.reason==="NO_VALID_INDEX"?"Няма валиден зонален ТИ.":z.source==="INDEX"?"В допустимия диапазон.":"Подробната причина още не е налична.";
  }
}
export function HrSpeedZones({model,admission,indexWindow,zoneSource,speedRole}:{model:HrModel;speedRole?:SpeedRole;admission?:SpeedModel["index_admission"];indexWindow?:SpeedModel["index_window"];zoneSource?:SpeedModel["hr_zone_source"]}){
  const rawSpeed=isRawMovingSpeed({speed_role:speedRole??model.speed_role});
  const heuristic=rawSpeed&&model.zones.some(z=>z.source==="INITIAL_HEURISTIC_V50_REFERENCE_SCALE");
  const conflict=model.zones.some(z=>z.reason==="CONFLICTING_ZONE_ANCHORS");
  const day=(value:string)=>value.split("-").reverse().join(".");
  return <section className="history-section">
    <h2>Връзка пулс–скорост по зони</h2>
    {zoneSource==="AUTOMATIC_HRMAX"&&<p>Пулсовите граници са автоматични по максималния пулс. <Link href="/?settings=edit">Прегледай зоните в настройките →</Link></p>}
    {indexWindow&&<p>ТИ за избрания спорт: последните {indexWindow.days} календарни дни ({day(indexWindow.start)}–{day(indexWindow.end)}), включително днес. {indexWindow.last_activity_date?`Последна включена тренировка: ${day(indexWindow.last_activity_date)}.`:"Няма подходящ индекс в този период; използват се експертните ориентири."}</p>}
    <p>Границите са за максимално непрекъснато усилие, не за продължителността на тренировката. Z1–Z4 са при горната пулсова граница; Z5 започва от общата граница със Z4.</p>
    <p>{heuristic?"Началните граници използват прогнозния праг като временна V50 опора и мащабирана нормативна форма. Връзката с пулса е моделна оценка, а не измерен индивидуален ТИ. Скоростта е в движение, без корекция за релефа.":"Наблюдаваният ТИ определя съответстващите пулсови и скоростни граници. При липса на ТИ използваме началната оценка от V(50 min) и V(6 min)."} Времевият капацитет се оценява отделно и не променя приетите граници.</p>
    {model.curve_duration_range_s && model.curve_speed_range_kmh && <p>Обхват на кривата: {n(model.curve_speed_range_kmh[0])}–{n(model.curve_speed_range_kmh[1])} км/ч · {duration(model.curve_duration_range_s[0])}–{duration(model.curve_duration_range_s[1])}. Извън него не екстраполираме време.</p>}
    {conflict && <p role="status">Конфликт между зоните{model.conflicting_zones?.length?` ${model.conflicting_zones.map(pair=>pair.join("–")).join(", ")}`:""}: при по-висок пулс времето не намалява. Затова Z1–Z4 използват съгласувани експертни времеви ориентири. Първоначалните резултати от ТИ са запазени по-долу.</p>}
    <div className="activity-table-wrap"><table aria-label="Използвана връзка пулс–скорост"><thead><tr><th>Зона</th><th>Пулс</th><th>{rawSpeed?"Скорост в движение · оценка":"Скоростен ориентир · Vflat"}</th><th>Основа</th></tr></thead><tbody>{model.zones.map(z=><tr key={z.zone}><th>{z.zone}</th><td>{n(z.hr_bpm)} уд./мин</td><td>{n(z.speed_kmh)} км/ч</td><td>{z.source==="Z4_SHARED_BOUNDARY"?"Обща граница със Z4":sourceLabel(z.source)}</td></tr>)}</tbody></table></div>
    <details><summary>Времеви граници и проверка на индекса</summary><div className="activity-table-wrap"><table aria-label="Диагностика на връзката пулс–скорост">
      <thead><tr><th>Зона / пулс</th><th>ТИ / тренировки</th><th>Скорост от ТИ</th><th>Време от ТИ</th><th>Експертен времеви ориентир</th><th>Използвано време / {rawSpeed?"скорост в движение":"Vflat"}</th><th>Решение и причина</th></tr></thead>
      <tbody>{model.zones.map(z=><tr key={z.zone}>
        <th scope="row">{z.zone}<br/>{n(z.hr_bpm)} уд./мин</th>
        <td>{z.index==null?"—":n(z.index)}{z.source!=="Z4_SHARED_BOUNDARY"&&<><br/>{z.count} тренировки</>}</td>
        <td>{z.candidate_speed_kmh==null?"—":`${n(z.candidate_speed_kmh)} км/ч`}</td>
        <td>{z.candidate_duration_s==null?"—":duration(z.candidate_duration_s)}</td>
        <td>{duration(z.duration_min_s)}–{duration(z.duration_max_s)}</td>
        <td>{duration(z.duration_s)}<br/>{n(z.speed_kmh)} км/ч</td>
        <td style={{whiteSpace:"normal",minWidth:220,maxWidth:300}}>{z.source==="Z4_SHARED_BOUNDARY"?"Обща граница със Z4; не се изчислява отделно от ТИ за Z5.":<>
          <strong>{sourceLabel(z.source)}</strong><br/>
          {candidateExplanation(z)}
          {z.reason==="CONFLICTING_ZONE_ANCHORS"&&<> Обща замяна заради конфликт между зоните.</>}
        </>}</td>
      </tr>)}</tbody>
    </table></div></details>
    {admission&&<p>{admission.activities} скорошни тренировки{admission.used!==undefined&&<> · {admission.used} с използван индекс</>} · {admission.excluded} изключени от ТИ · {admission.refresh_required} изискват обновяване{Boolean(admission.incompatible)&&<> · {admission.incompatible} с несъпоставими настройки</>}.</p>}
  </section>;
}
