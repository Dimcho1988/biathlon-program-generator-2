"use client";
import { useState, type CSSProperties } from "react";
import { MODEL_ZONES,type ModelZone,type RecoveryV2 } from "../lib/models";
import { RecoveryTimeline } from "./recovery-timeline";
import { RecoverySettingsEditor } from "./recovery-settings-editor";
const n=(v:number)=>new Intl.NumberFormat("bg-BG",{maximumFractionDigits:2}).format(v);
const sourceLabels={PERSONAL:"Лична средна",SHORT_HISTORY:"Кратка история · смесена база",SPARSE_ZONE_HISTORY:"Малко товар в зоната · смесена база",NO_HISTORY:"Начална база · няма история",NO_ZONE_LOAD:"Начална база · няма товар в зоната"};
const additiveSourceLabels={PERSONAL:"Лична средна + добавка",SHORT_HISTORY:"Кратка история + добавка",SPARSE_ZONE_HISTORY:"Малко товар в зоната + добавка",NO_HISTORY:"Базова добавка · няма история",NO_ZONE_LOAD:"Базова добавка · няма товар в зоната"};
export function RecoveryV2Section({history,canEdit}:{history:RecoveryV2;canEdit:boolean}){
  const [zone,setZone]=useState<ModelZone>("Z2");
  const permanentBase=history.model.algorithm_version==="recovery-daily-e-biexponential-v2.2";
  const contributors=history.daily.filter(row=>row.zone===zone&&(row.residual_fatigue_now??0)>=.01).sort((a,b)=>(b.residual_fatigue_now??0)-(a.residual_fatigue_now??0));
  return <section className="history-section recovery-section" aria-labelledby="recovery-title">
    <div className="section-heading"><div><p className="section-kicker">Индивидуално възстановяване</p><h2 id="recovery-title">Готовност по зони · праг 90%</h2></div><p>Към {history.as_of}</p></div>
    <p>Товарът E включва каскадата и разливането. Новите натоварвания се добавят към остатъчната умора. Изчислението е по календарни дни; тренировките в един ден се сумират.</p>
    {history.source_stale&&<p role="status" className="integration-notice">Последният товар е до {history.source_as_of}. Прогнозата допуска, че след това няма ново натоварване. Обновете активностите.</p>}
    {permanentBase&&<p>Базата за Recovery е постоянната добавка за зоната + личният среднодневен товар за предходните до 40 дни. Добавката участва винаги и сама по себе си не създава умора.</p>}
    <div className="load-summary recovery-summary">{history.current.map(c=><article key={c.zone} className="load-summary-card" style={{"--series":c.zone==="STR"?"var(--strength)":`var(--zone-${c.zone.slice(1)})`} as CSSProperties}>
      <div><span className="summary-zone">{c.zone}</span><strong>{n(c.readiness_percent)}%</strong><small>{c.readiness_percent>=90?"достигнат праг":"в процес на възстановяване"}</small></div>
      <dl><div><dt>До 90% готовност</dt><dd>{n(c.days_to_practical_recovery)} дни</dd></div><div><dt>{permanentBase?"База за Recovery":"Среднодневна база"}</dt><dd>{n(c.baseline_daily_min)} мин E/ден</dd></div>
        {permanentBase&&<><div><dt>Постоянна добавка</dt><dd>{n(history.settings[c.zone].initial_daily_min)} мин E/ден</dd></div><div><dt>Лична средна</dt><dd>{c.baseline_raw_daily_min===null?"няма история":`${n(c.baseline_raw_daily_min)} мин E/ден`}</dd></div></>}
      </dl><small>{(permanentBase?additiveSourceLabels:sourceLabels)[c.baseline_source]} · {c.history_days}/40 дни</small>
    </article>)}</div>
    <RecoveryTimeline history={history}/>
    <RecoverySettingsEditor history={history} canEdit={canEdit}/>
    <details className="recovery-provenance"><summary>Защо срокът е такъв? · Дневна история и произход</summary>
      <label>Зона за подробности <select value={zone} onChange={e=>setZone(e.target.value as ModelZone)}>{MODEL_ZONES.map(z=><option key={z}>{z}</option>)}</select></label>
      <p>Срокът за {zone} включва остатъка от всички предишни натоварвания. {permanentBase
        ?"За всеки ден началната продължителност е товарът E ÷ (постоянната добавка + личната средна за предходните до 40 дни) × коефициента за продължителност. Таблицата показва целия делител за съответния ден."
        :"За всеки ден началната продължителност е товарът E ÷ тогавашната среднодневна база × коефициента за продължителност. При много малка база дори няколко минути могат да дадат дълъг срок."}</p>
      {contributors.length>0&&<><h3>Дни с най-голяма остатъчна умора в {zone}</h3><div className="activity-table-wrap"><table><thead><tr><th>Ден на товара</th><th>E, мин</th><th>База тогава, мин/ден</th><th>До 90% само от този товар</th><th>Остатъчна умора днес, п.п.</th></tr></thead><tbody>{contributors.slice(0,8).map(d=><tr key={d.date}><th>{d.date}</th><td>{n(d.effective_load)}</td><td>{n(d.baseline_daily_min)}</td><td>{n(d.isolated_days_to_90)} дни</td><td>{n(d.residual_fatigue_now!)}</td></tr>)}</tbody></table></div><p>Изолираният срок се брои от деня на товара. В картата горе срокът се брои от днес и включва цялата натрупана умора. Показани са до 8 дни с остатък поне 0,01 п.п.</p></>}
      <p>Товарът E може да включва разливане от други зони и не е равен непременно на реалните минути в тази пулсова зона.</p>
      <details><summary>Всички отчетени дни за {zone}</summary><div className="activity-table-wrap"><table><thead><tr><th>Дата</th><th>E, мин</th><th>База, мин/ден</th><th>Готовност след товара</th></tr></thead><tbody>{history.daily.filter(d=>d.zone===zone).map(d=><tr key={d.date}><td>{d.date}</td><td>{n(d.effective_load)}</td><td>{n(d.baseline_daily_min)}</td><td>{n(d.readiness_after_percent)}%</td></tr>)}</tbody></table></div></details>
      <p>Експертен модел за калибриране. Умората преди началото на наличната история е неизвестна. {permanentBase&&"Recovery v2.2 · постоянна базова добавка. "}Версия {history.model.parameter_version}.</p>
    </details>
  </section>;
}
