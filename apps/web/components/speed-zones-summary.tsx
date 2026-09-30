"use client";
import {useState} from "react";
import type {SpeedZoneProfile,StandardizedCS} from "../lib/models";
import {isRecord} from "../lib/training-status";
import {componentColor} from "../lib/training-visuals";

const n=(v:number)=>v.toLocaleString("bg-BG",{maximumFractionDigits:2});
export function StandardizedCriticalSpeed({value}:{value?:StandardizedCS}) {
  if(value?.speed_kmh==null)return null;
  return <div className="speed-curve-readout"><p><strong>CS по 3 и 12 мин: {n(value.speed_kmh)} км/ч</strong> · оценка от индивидуалната крива.</p>
    <p>{value.points.map(p=>`${p.duration_s/60} мин: ${n(p.speed_kmh)} км/ч`).join(" · ")}. {value.uses_extrapolation?"Използвана е и екстраполация извън реалните тестове.":"Стойностите са в диапазона на реалните тестове."}</p>
    <p>Реалните тестове остават опорите на кривата. Ограничението 5% описва продължението ѝ; не е оценка на грешката на CS.</p></div>;
}
export function SpeedZonesSummary({profile,sport,generation,revision}:{profile?:SpeedZoneProfile;sport:string;generation?:string|null;revision?:number|null}) {
  const [history,setHistory]=useState<Record<string,unknown>|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState("");
  if(profile?.status!=="AVAILABLE")return null;
  async function read() {
    setBusy(true);setError("");
    try {
      const r=await fetch(`/api/athlete/models/speed-history?${new URLSearchParams({sport})}`);
      const data:unknown=await r.json();
      if(!r.ok||!isRecord(data)||data.status!=="AVAILABLE"||!Array.isArray(data.zones))throw new Error("Историята не е достъпна или моделът е обновен. Опитай отново.");
      if(data.source_generation_id!==generation||data.source_revision!==revision)throw new Error("Моделът е обновен. Презареди страницата, за да сравним историята с текущите зони.");
      setHistory(data);
    }catch(e){setError(e instanceof Error?e.message:"Историята не е достъпна.");}
    finally{setBusy(false);}
  }
  const totals=Array.isArray(history?.zones)?history.zones.filter(isRecord):[];
  return <section className="history-section"><h2>Скоростни зони</h2><p>Скорости, приравнени към равен терен. Границите използват индивидуалната връзка с пулса или времевите ориентири от кривата.</p>
    <div className="activity-table-wrap"><table><thead><tr><th>Зона</th><th>км/ч</th><th>Основа</th>{history&&<th>Време за 40 дни</th>}</tr></thead><tbody>{profile.zones.map(z=>{const total=totals.find(r=>r.zone===z.zone);return <tr key={z.zone}><th style={{color:componentColor(z.zone)}}>{z.zone}</th><td>{z.high_kmh==null?`над ${n(z.low_kmh)}`:`${n(z.low_kmh)}–${n(z.high_kmh)}`}</td><td>{z.source==="PAIRED_INDEX"?"Индивидуален индекс":"Оценка от кривата"}</td>{history&&<td>{typeof total?.minutes==="number"?`${n(total.minutes)} мин`:"—"}</td>}</tr>;})}</tbody></table></div>
    <button type="button" className="action-button secondary" disabled={busy} onClick={read}>{busy?"Зареждане…":history?"Обнови скоростната история":"Покажи времето по скоростни зони · 40 дни"}</button>
    <div role="status">{error&&<p>{error}</p>}{history&&<p>Класифицирани по надеждна скорост: {n(Number(history.classified_minutes))} мин. Това е отделен отчет за външната работа; не се добавя повторно към пулсовото натоварване.</p>}</div></section>;
}
