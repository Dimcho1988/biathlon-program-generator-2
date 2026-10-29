"use client";
import { isRawMovingSpeed } from "../lib/speed-basis";
import { SpeedEquivalenceComparison } from "./speed-equivalence-comparison";
import { PreferredSpeed, RunningSpeedUnitControl } from "./running-speed-unit";
import { durationHms } from "../lib/duration-format";
import {useEffect,useRef,useState} from "react";
import { MetricChart } from "./metric-chart";
import type {SpeedLoad} from "../lib/speed-load";
import {readSpeedLoad} from "../lib/speed-load-client";
import {componentColor} from "../lib/training-visuals";

const n=(v:number|null|undefined)=>v==null?"—":v.toLocaleString("bg-BG",{maximumFractionDigits:2});
type Props={generation:string|null;revision:number|null;cacheScope?:string};
export function SpeedLoadSummary(props:Props) {
  return <SpeedLoadSummaryView key={`${props.cacheScope}:${props.generation}:${props.revision}`} {...props}/>;
}
function SpeedLoadSummaryView({generation,revision,cacheScope}:Props) {
  const [data,setData]=useState<SpeedLoad|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState("");
  const [sport,setSport]=useState(""),[zone,setZone]=useState("Z1");
  const request=useRef<AbortController|null>(null);
  useEffect(()=>()=>request.current?.abort(),[]);
  async function read(selected=sport,force=false) {
    if(request.current)return;
    const controller=new AbortController();request.current=controller;
    setBusy(true);setError("");
    try {
      const result=await readSpeedLoad({cacheScope,generation,revision,sport:selected,force,signal:controller.signal,
        errorMessage:"Отчетът временно не е достъпен. Опитай отново.",
        generationError:"Данните са обновени. Презареди страницата за съгласуван отчет."});
      if(!controller.signal.aborted){setData(result);setSport(selected);}
    }catch(e){if(!controller.signal.aborted){setData(null);setError(e instanceof Error?e.message:"Неуспешно зареждане.");}}
    finally{if(!controller.signal.aborted){request.current=null;setBusy(false);}}
  }
  const rows=data?.daily.filter(d=>d.zone===zone)??[];
  const ymax=Math.max(1.5,...rows.map(d=>d.ratio_7_40??0))*1.1;
  return <section className="history-section" aria-label="Натоварване по скорост">
    <h2>Натоварване по скорост · 40 дни</h2>
    <RunningSpeedUnitControl/>
    <p>Скоростта се превръща в зона чрез приложимия профил за датата на тренировката. Индивидуалните опори използват Vflat; при липсващи тестове и валиден индекс началният модел използва скоростта в движение без корекция за релефа. Бягане, ролки/ски и колело се събират по общата интензивност. Отчетът показва скоростната сметка. Водещият показател за основното натоварване се избира от настройките на профила.</p>
    <button type="button" className="action-button secondary" disabled={busy} onClick={()=>read(sport,Boolean(data))}>{busy?"Изчисляване…":data?"Обнови отчета по скорост":"Изчисли товара по скорост"}</button>
    <div role="status">{error&&<p>{error}</p>}</div>
    {data&&<>
      {data.activities.some(a=>a.initial_heuristic||isRawMovingSpeed(a))&&<p>Част от товара е изчислена по начални моделни зони и скорост в движение. Тази част е оценка от историята, без индивидуална тестова опора.</p>}
      <SpeedEquivalenceComparison value={data.speed_equivalence_comparison}/>
      <label>Обхват <select value={sport} disabled={busy} onChange={e=>read(e.target.value)}><option value="">Всички поддържани спортове</option>{data.sports.map(s=><option key={s}>{s}</option>)}</select></label>
      <p><strong>Покритие: {n(data.coverage_percent)}%</strong> · {durationHms(data.classified_minutes)} от {durationHms(data.recorded_minutes)}. {data.status==="UNAVAILABLE"?"Нужни са валидни индекси от по-ранни дни и преизчислени активности.":"Q, E и 7/40 описват само отчетената част от историята."}</p>
      {data.rest_minutes !== undefined && <p>Отделно отчетена почивка с Q = 0: {durationHms(data.rest_minutes)} · време без оценка: {durationHms(data.unaccounted_minutes ?? 0)}.</p>}
      {(data.estimated_descent_minutes ?? 0) > 0 && <p>Ски спускания с оценена приравнена скорост: {durationHms(data.estimated_descent_minutes)}.</p>}
      {data.warnings.includes("TREADMILL_GRADE_ASSUMED_FLAT")&&<p>Пътека: при липсващ запис за наклона използваме скоростта с допускане за 0% наклон.</p>}
      {data.warnings.includes("TREADMILL_PRIOR_RUN_INDEX_FALLBACK")&&<p>За пътека без предходен собствен индекс е използван предходният индекс от бягане на същия спортист.</p>}
      {(data.warnings.includes("SPEED_RECOMPUTATION_REQUIRED") || data.activities.some(a=>a.reason==="SPEED_RECOMPUTATION_REQUIRED"))&&<p>Има активности със стар модел. Използвай „Обнови данните“ на началния екран.</p>}
      <div className="activity-table-wrap"><table><caption>Индекси и скорости при еднакъв приравнен пулс · последни 40 дни</caption><thead><tr><th>Спорт</th><th>Общ ТИ</th><th>Референтен пулс</th><th>Пулс за спорта</th><th>Темпо / скорост · основа</th></tr></thead><tbody>{data.sport_indices.map(s=><tr key={s.sport}><th>{s.sport}</th><td>{n(s.indices.GENERAL?.index)}</td><td>{n(s.reference_hr_bpm)}</td><td>{n(s.reference_hr_bpm-s.hr_policy.offset_bpm)}</td><td><PreferredSpeed speed={s.comparison_speed_kmh} sport={s.sport}/>{s.mapping?.uses_general_index&&" *"}<br/><small>{isRawMovingSpeed(s.mapping)?"Скорост в движение · начална оценка":"Vflat"}</small></td></tr>)}</tbody></table></div>
      {data.sport_indices.some(s=>s.mapping?.uses_general_index)&&<p>* Използван е общият индекс за същия спорт, когато зоналните индекси липсват или дават несъгласувани скоростни граници. Това е оценка на връзката, а не нов зонален тест.</p>}
      {data.sport_indices.some(s=>s.hr_policy.offset_bpm>0)&&<p>Колело: +7 уд./мин за съпоставяне; целевите зони са със 7 удара по-ниски. Начална треньорска настройка. Vflat използва обща маса 80 kg, Crr 0,005, CdA 0,32 m² и безветрие; настилката и вятърът могат да променят оценката.</p>}
      <div className="activity-table-wrap"><table><thead><tr><th>Зона</th><th>Време · ч:мм:сс</th><th>Q · ч:мм:сс</th><th>E, ефективен товар</th><th>7/40</th></tr></thead><tbody>{data.zones.map(z=><tr key={z.zone}><th style={{color:componentColor(z.zone)}}>{z.zone}</th><td>{durationHms(z.minutes)}</td><td>{durationHms(z.equivalent_minutes)}</td><td>{n(z.effective_load)}</td><td>{n(z.ratio_7_40)}</td></tr>)}</tbody></table></div>
      <p>Q използва същите коефициенти като пулсовия отчет. E добавя към всяка по-ниска зона прекия Q на Z2 × 1,4, Z3 × 1,8, Z4/Z5 × 2,3. Това са общи експертни множители, без още 100% отгоре. Допълнително към непосредствените съседи според Q/Tmax: от 50% до 80% — 20% нагоре и 10% надолу; над 80% — 30% и 20%. Процентите са от целия пряк Q, без повторно преливане. Tmax идва от индивидуалната крива или от експертна оценка при липсваща използваема крива. 7/40 сравнява средния дневен E за 7 и 40 дни с базовата добавка.</p>
      {data.status!=="UNAVAILABLE"&&<><label>Зона за динамиката <select value={zone} onChange={e=>setZone(e.target.value)}>{data.zones.map(z=><option key={z.zone}>{z.zone}</option>)}</select></label>
        <figure className="history-chart"><MetricChart title={`Динамика 7/40 по скорост за ${zone}`} unit="Индекс 7/40" domain={[0,ymax]} maxGap={86400000} reference={{value:1,label:"7/40 = 1 · стабилен товар"}} series={[{ key: zone, label: zone, color: componentColor(zone), points: rows.map(row => ({x:Date.parse(row.date),y:row.ratio_7_40})) }]} /></figure></>}
    </>}
  </section>;
}
