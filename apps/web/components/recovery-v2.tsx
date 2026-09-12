"use client";
import { useState, type CSSProperties } from "react";
import { useRouter } from "next/navigation";
import { MODEL_ZONES,saveModel,type ModelZone,type RecoveryV2 } from "../lib/models";
const n=(v:number)=>new Intl.NumberFormat("bg-BG",{maximumFractionDigits:2}).format(v);
const sourceLabels={PERSONAL:"Лична средна",SHORT_HISTORY:"Кратка история · смесена база",SPARSE_ZONE_HISTORY:"Малко товар в зоната · смесена база",NO_HISTORY:"Начална база · няма история",NO_ZONE_LOAD:"Начална база · няма товар в зоната"};
export function RecoveryV2Section({history,canEdit}:{history:RecoveryV2;canEdit:boolean}){
  const router=useRouter();
  const [zone,setZone]=useState<ModelZone>("Z2");
  const [settings,setSettings]=useState(history.settings);
  const [busy,setBusy]=useState(false),[message,setMessage]=useState("");
  const forecast=history.forecast.filter(p=>p.zone===zone);
  const end=forecast.at(-1)?.days||1;
  async function save(){
    setBusy(true);setMessage("");
    try{await saveModel("recovery",{expected_revision:history.config_revision,zones:settings});setMessage("Коефициентите са запазени. Преизчисляваме историята.");router.refresh();}
    catch(e){setMessage(e instanceof Error?e.message:"Грешка при запис.");}finally{setBusy(false);}
  }
  return <section className="history-section recovery-section" aria-labelledby="recovery-title">
    <div className="section-heading"><div><p className="section-kicker">Индивидуално възстановяване</p><h2 id="recovery-title">Готовност по зони · праг 90%</h2></div><p>Към {history.as_of}</p></div>
    <p>Товарът E включва каскадата и разливането. Новите натоварвания се добавят към остатъчната умора. Изчислението е по календарни дни; тренировките в един ден се сумират.</p>
    {history.source_stale&&<p role="status" className="integration-notice">Последният товар е до {history.source_as_of}. Прогнозата допуска, че след това няма ново натоварване. Обновете активностите.</p>}
    <div className="load-summary recovery-summary">{history.current.map(c=><article key={c.zone} className="load-summary-card" style={{"--series":c.zone==="STR"?"var(--strength)":`var(--zone-${c.zone.slice(1)})`} as CSSProperties}><div><span className="summary-zone">{c.zone}</span><strong>{n(c.readiness_percent)}%</strong><small>{c.readiness_percent>=90?"достигнат праг":"в процес на възстановяване"}</small></div><dl><div><dt>До 90% готовност</dt><dd>{n(c.days_to_practical_recovery)} дни</dd></div><div><dt>Среднодневна база</dt><dd>{n(c.baseline_daily_min)} мин E</dd></div></dl><small>{sourceLabels[c.baseline_source]} · {c.history_days}/40 дни</small></article>)}</div>
    <label>Възстановителна крива <select value={zone} onChange={e=>setZone(e.target.value as ModelZone)}>{MODEL_ZONES.map(z=><option key={z}>{z}</option>)}</select></label>
    <figure className="history-chart"><svg viewBox="0 0 920 270" role="img" aria-label={`Прогнозирано възстановяване в ${zone} при липса на нови тренировки`}>
      {[0,50,90,100].map(v=><g key={v}><line x1="48" x2="900" y1={230-2*v} y2={230-2*v} stroke="currentColor" opacity={.15}/><text x="40" y={234-2*v} textAnchor="end" fill="currentColor" fontSize="12">{v}%</text></g>)}
      <polyline fill="none" stroke="var(--accent, #41b88c)" strokeWidth="3" points={forecast.map(p=>`${48+852*p.days/end},${230-2*p.readiness_percent}`).join(" ")}/>
      <text x="48" y="257" fill="currentColor" fontSize="12">Сега</text><text x="900" y="257" textAnchor="end" fill="currentColor" fontSize="12">След {n(end)} дни</text>
    </svg><figcaption>Остатъчната умора от всички отчетени дни. Допускане: без следваща тренировка.</figcaption></figure>
    <details className="recovery-settings"><summary>Донастройване по зони</summary>
      <p>Продължителност × 1 е началният модел. Стръмност 1 е обикновена експонента; по-висока стойност засилва бързата начална фаза при същия срок до 90% за съществено натоварване. При малък товар близо до прага готовността може да настъпи по-рано.</p>
      <div className="activity-table-wrap"><table><thead><tr><th>Зона</th><th>Продължителност ×</th><th>Стръмност</th><th>Чувствителност</th><th>Начална база, мин/ден</th></tr></thead><tbody>{MODEL_ZONES.map(z=><tr key={z}><th>{z}</th>{(["duration_coefficient","shape","sensitivity","initial_daily_min"] as const).map(key=><td key={key}><input type="number" aria-label={`${z} ${key}`} disabled={!canEdit||busy} min={key==="shape"?1:key==="sensitivity"?.05:.1} max={key==="shape"?10:key==="initial_daily_min"?600:key==="sensitivity"?3:5} step={key==="shape"?.1:.05} value={settings[z][key]} onChange={e=>setSettings({...settings,[z]:{...settings[z],[key]:Number(e.target.value)}})} style={{width:"6rem"}}/></td>)}</tr>)}</tbody></table></div>
      <p>Чувствителността определя първоначалната умора. Началната база плавно отстъпва при поне 7 дни история и натрупан товар, равен на 7 начални дневни дози. При нулев или съвсем малък товар остава експертна опора.</p>
      {canEdit&&<button type="button" className="action-button" disabled={busy} onClick={save}>{busy?"Записване…":"Запази и преизчисли"}</button>}<p role="status">{message}</p>
    </details>
    <details><summary>Дневна история и произход</summary><p>Експертен модел за калибриране. Умората преди началото на наличната история е неизвестна. Версия {history.model.parameter_version}.</p><div className="activity-table-wrap"><table><thead><tr><th>Дата</th><th>E, мин</th><th>База, мин/ден</th><th>Готовност след товара</th></tr></thead><tbody>{history.daily.filter(d=>d.zone===zone).map(d=><tr key={d.date}><td>{d.date}</td><td>{n(d.effective_load)}</td><td>{n(d.baseline_daily_min)}</td><td>{n(d.readiness_after_percent)}%</td></tr>)}</tbody></table></div></details>
  </section>;
}
