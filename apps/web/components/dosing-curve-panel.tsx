"use client";
import {useState} from "react";
import type {DosingModel} from "../lib/models";
import {clockTime} from "../lib/speed-tests";

const n=(v:number)=>new Intl.NumberFormat("bg-BG",{maximumFractionDigits:2}).format(v);
const series=[
  {key:"index_speed_kmh",label:"От индекса",color:"#398bff",dash:"5 4"},
  {key:"test_speed_kmh",label:"От максималните тестове",color:"#f59e0b",dash:undefined},
  {key:"speed_kmh",label:"Обща за дозиране · 30/70",color:"#be77ff",dash:undefined},
] as const;

export function DosingCurvePanel({model}:{model:DosingModel}){
  const [cursor,setCursor]=useState(90);
  const [visible,setVisible]=useState([true,true,true]);
  if(model.status!=="AVAILABLE"||model.points.length<2)return <section className="history-section"><h2>Обща крива за дозиране · 30/70</h2><p>Нужни са едновременно съпоставим индекс и активен максимален тест за този спорт. Дотогава методите използват наличните индивидуални или експертни ориентири.</p></section>;
  const points=model.points,sample=points[Math.min(cursor,points.length-1)];
  const lo=Math.log(points[0].duration_s),hi=Math.log(points.at(-1)!.duration_s);
  const vmax=Math.max(...points.map(p=>Math.max(p.index_speed_kmh,p.test_speed_kmh)))*1.05;
  const x=(t:number)=>48+852*(Math.log(t)-lo)/(hi-lo),y=(v:number)=>276-246*v/vmax;
  const zones=model.zone_comparison??[],last=zones.at(-1);
  return <section className="history-section" id="dosing-curve">
    <h2>Обща крива за дозиране · 30/70</h2>
    <p>При еднаква продължителност взимаме 30% от скоростта по индексната крива и 70% от скоростта по реалните максимални тестове. Методите използват този общ ориентир за скорост и устойчивост. Съотношението 30/70 е избрано експертно правило; предстои проверка чрез реалното изпълнение.</p>
    <div className="model-controls" role="group" aria-label="Показани криви">{series.map((s,i)=><button key={s.key} type="button" className="action-button secondary" aria-pressed={visible[i]} onClick={()=>setVisible(v=>v.map((on,j)=>j===i?!on:on))}><span aria-hidden="true" style={{color:s.color}}>● </span>{s.label}</button>)}</div>
    <figure className="history-chart"><svg viewBox="0 0 920 320" role="img" aria-label="Индексна, тестова и обща крива за дозиране">
      {[0,.25,.5,.75,1].map(f=><g key={f}><line x1="48" x2="900" y1={y(vmax*f)} y2={y(vmax*f)} stroke="currentColor" opacity=".12"/><text x="40" y={y(vmax*f)+4} textAnchor="end" fill="currentColor" fontSize="12">{n(vmax*f)}</text></g>)}
      {series.map((s,i)=>visible[i]&&<polyline key={s.key} data-curve={s.key} points={points.map(p=>`${x(p.duration_s)},${y(p[s.key])}`).join(" ")} fill="none" stroke={s.color} strokeWidth={i===2?4:2} strokeDasharray={s.dash}/>)}
      <line x1={x(sample.duration_s)} x2={x(sample.duration_s)} y1="30" y2="276" stroke="currentColor" opacity=".3"/>
      {series.map((s,i)=>visible[i]&&<circle key={s.key} cx={x(sample.duration_s)} cy={y(sample[s.key])} r="4" fill={s.color}/>)}
      {[60,180,720,3600,21600].filter(t=>Math.log(t)>=lo&&Math.log(t)<=hi).map(t=><text key={t} x={x(t)} y="300" textAnchor="middle" fill="currentColor" fontSize="12">{clockTime(t)}</text>)}
      <text x="48" y="20" fill="currentColor" fontSize="12">км/ч · равен терен</text>
    </svg><figcaption>Трите криви са отделни. Общата крива е работна оценка за дозиране и не представлява нов максимален тест.</figcaption></figure>
    <div className="speed-curve-readout"><strong>{clockTime(sample.duration_s)} · Обща скорост {n(sample.speed_kmh)} км/ч</strong><span>Индекс {n(sample.index_speed_kmh)} · Тестове {n(sample.test_speed_kmh)} км/ч · {sample.estimated_hr_bpm===null?"Пулс: извън поддържания обхват":`Оценен пулс ${n(sample.estimated_hr_bpm)} уд./мин`}</span><label>Разгледай общата крива<input type="range" min="0" max={points.length-1} step="1" value={Math.min(cursor,points.length-1)} onChange={e=>setCursor(Number(e.target.value))}/></label></div>
    <h3>Горни граници на зоните за дозиране</h3><div className="activity-table-wrap"><table><thead><tr><th>Зона</th><th>Пулс</th><th>Времеви ориентир</th><th>От индекса, км/ч</th><th>От тестовете, км/ч</th><th>Обща скорост, км/ч</th></tr></thead><tbody>{zones.map(z=><tr key={z.zone}><th>{z.zone}</th><td>{n(z.hr_bpm)}</td><td>{clockTime(z.duration_s)}</td><td>{n(z.index_speed_kmh)}</td><td>{n(z.test_speed_kmh)}</td><td><strong>{n(z.speed_kmh)}</strong></td></tr>)}{last&&<tr><th>Z5</th><td>над {n(last.hr_bpm)}</td><td>Според усилието</td><td>над {n(last.index_speed_kmh)}</td><td>над {n(last.test_speed_kmh)}</td><td><strong>над {n(last.speed_kmh)}</strong></td></tr>}</tbody></table></div>
    <p>Времевите ориентири са оценки за непрекъсната устойчивост. Тренировъчната доза се определя отделно според метода, Q/E, 7/40, Recovery и настройките. При кратки интензивни повторения скоростта и качеството на изпълнение водят, а пулсът служи за наблюдение.</p>
  </section>;
}
