"use client";
import {useEffect,useRef,useState} from "react";
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
  const x=(i:number)=>45+815*i/Math.max(rows.length-1,1),y=(v:number)=>200-170*v/ymax;
  return <section className="history-section" aria-label="Натоварване по скорост">
    <h2>Натоварване по скорост · 40 дни</h2>
    <p>Приравнената скорост се превръща в зона чрез предходните индекси за същия спорт. Бягане, ролки/ски и колело се събират по общата интензивност. Това е самостоятелна оценка, която не се добавя към пулсовия товар.</p>
    <button type="button" className="action-button secondary" disabled={busy} onClick={()=>read(sport,Boolean(data))}>{busy?"Изчисляване…":data?"Обнови отчета по скорост":"Изчисли товара по скорост"}</button>
    <div role="status">{error&&<p>{error}</p>}</div>
    {data&&<>
      <label>Обхват <select value={sport} disabled={busy} onChange={e=>read(e.target.value)}><option value="">Всички поддържани спортове</option>{data.sports.map(s=><option key={s}>{s}</option>)}</select></label>
      <p><strong>Покритие: {n(data.coverage_percent)}%</strong> · {n(data.classified_minutes)} от {n(data.recorded_minutes)} мин. {data.status==="UNAVAILABLE"?"Нужни са валидни индекси от по-ранни дни и преизчислени активности.":"Q, E и 7/40 описват само отчетената част от историята."}</p>
      {data.warnings.includes("TREADMILL_GRADE_ASSUMED_FLAT")&&<p>Пътека: при липсващ запис за наклона използваме скоростта с допускане за 0% наклон.</p>}
      {data.warnings.includes("TREADMILL_PRIOR_RUN_INDEX_FALLBACK")&&<p>За пътека без предходен собствен индекс е използван предходният индекс от бягане на същия спортист.</p>}
      {data.activities.some(a=>a.reason==="SPEED_RECOMPUTATION_REQUIRED")&&<p>Има активности със стар модел. Използвай „Обнови данните“ на началния екран.</p>}
      <div className="activity-table-wrap"><table><caption>Индекси и скорости при еднакъв приравнен пулс · последни 40 дни</caption><thead><tr><th>Спорт</th><th>Общ ТИ</th><th>Референтен пулс</th><th>Пулс за спорта</th><th>Vflat, км/ч</th></tr></thead><tbody>{data.sport_indices.map(s=><tr key={s.sport}><th>{s.sport}</th><td>{n(s.indices.GENERAL?.index)}</td><td>{n(s.reference_hr_bpm)}</td><td>{n(s.reference_hr_bpm-s.hr_policy.offset_bpm)}</td><td>{n(s.comparison_speed_kmh)}{s.mapping?.uses_general_index&&" *"}</td></tr>)}</tbody></table></div>
      {data.sport_indices.some(s=>s.mapping?.uses_general_index)&&<p>* Използван е общият индекс за същия спорт, когато зоналните индекси липсват или дават несъгласувани скоростни граници. Това е оценка на връзката, а не нов зонален тест.</p>}
      {data.sport_indices.some(s=>s.hr_policy.offset_bpm>0)&&<p>Колело: +7 уд./мин за съпоставяне; целевите зони са със 7 удара по-ниски. Начална треньорска настройка. Vflat използва обща маса 80 kg, Crr 0,005, CdA 0,32 m² и безветрие; настилката и вятърът могат да променят оценката.</p>}
      <div className="activity-table-wrap"><table><thead><tr><th>Зона</th><th>Време, мин</th><th>Q, екв. мин</th><th>E, ефективен товар</th><th>7/40</th></tr></thead><tbody>{data.zones.map(z=><tr key={z.zone}><th style={{color:componentColor(z.zone)}}>{z.zone}</th><td>{n(z.minutes)}</td><td>{n(z.equivalent_minutes)}</td><td>{n(z.effective_load)}</td><td>{n(z.ratio_7_40)}</td></tr>)}</tbody></table></div>
      <p>Q използва същите коефициенти като пулсовия отчет, а E — същата каскада между зоните. За всяка тренировка скалата се определя само от предходните 40 дни. 7/40 сравнява средния дневен E за 7 и 40 дни с базовата добавка.</p>
      {data.status!=="UNAVAILABLE"&&<><label>Зона за динамиката <select value={zone} onChange={e=>setZone(e.target.value)}>{data.zones.map(z=><option key={z.zone}>{z.zone}</option>)}</select></label>
        <figure className="history-chart"><svg viewBox="0 0 900 235" role="img" aria-label={`Динамика 7/40 по скорост за ${zone}`}>
          {[0,1,ymax].map(v=><g key={v}><line x1="45" x2="860" y1={y(v)} y2={y(v)} stroke="currentColor" opacity=".15"/><text x="38" y={y(v)+4} textAnchor="end" fontSize="12" fill="currentColor">{n(v)}</text></g>)}
          {rows.slice(1).map((d,i)=>d.ratio_7_40!==null&&rows[i].ratio_7_40!==null&&<line key={d.date} x1={x(i)} x2={x(i+1)} y1={y(rows[i].ratio_7_40!)} y2={y(d.ratio_7_40)} stroke={componentColor(zone)} strokeWidth="3"><title>{d.date}: {n(d.ratio_7_40)}</title></line>)}
          <text x="45" y="225" fontSize="12" fill="currentColor">{data.start_date}</text><text x="860" y="225" textAnchor="end" fontSize="12" fill="currentColor">{data.end_date}</text>
        </svg></figure></>}
    </>}
  </section>;
}
