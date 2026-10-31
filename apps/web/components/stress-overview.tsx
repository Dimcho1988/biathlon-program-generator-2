"use client";
import { MetricChart } from "./metric-chart";
import {useState} from "react";
import {CHANNEL_LABELS, GROUPS, GROUP_LABELS, STATES, type ResponseHistory, type Group} from "../lib/response-monitoring";

const colors:Record<string,string>={total:"var(--response-total-ink,#112839)",trend:"#81939e",subjective:"#008c95",functional:"#c87909",physiology:"#8055b5",weight:"#297cc5",biochemistry:"#be5278"};
const fmt=(v:number|null|undefined,digits=1)=>v==null?"—":v.toLocaleString("bg-BG",{maximumFractionDigits:digits});
const dateLabel=(s:string)=>`${s.slice(8,10)}.${s.slice(5,7)}`;
const units:Record<string,string>={ratio:"",TI:"ТИ",bpm:"уд./мин",ms:"ms",h:"ч",RPE:"/10","1–5":"/5"};
const sources:Record<string,string>={ONFLOWS:"Въведено в onFlows",INTERVALS:"Intervals",LAB:"Лабораторно измерване",HRMOD_VFLAT_AND_EXECUTION:"ТИ / съпоставимо изпълнение"};

export function StressOverview({history,selected,onSelect}:{history:ResponseHistory;selected:number;onSelect:(i:number)=>void}){
  const [visible,setVisible]=useState<Group[]>([]);
  const [indicator,setIndicator]=useState("");
  const day=history.days[selected];
  if(!day)return <section className="response-card"><h2>Все още няма наблюдения</h2></section>;
  const series=["total","trend",...visible,...(indicator?[indicator]:[])];
  const value=(i:number,key:string)=>key==="total"?history.days[i].total:key==="trend"?history.days[i].trend_3d:GROUPS.includes(key as Group)?history.days[i].groups.find(g=>g.key===key)?.score??null:history.days[i].channels.find(c=>c.key===key)?.score??null;
  const color=(key:string)=>colors[key]||colors[day.channels.find(c=>c.key===key)?.group??"functional"];

  return <>
    <section className="response-card stress-summary" aria-label="Обща оценка на стреса">
      <div className="response-heading"><div><p className="eyebrow">{dateLabel(day.day)} · {STATES[day.state]||day.state}</p><h2>Стрес и отзвучаване</h2></div><span className="response-badge">Пилотна оценка</span></div>
      <div className="stress-kpis"><div><strong>{fmt(day.total)}<small> / 100</small></strong><span>{day.assessment_quality==="PARTIAL"?"Частична оценка":day.total==null?"Няма оценка":"Обща оценка"}</span></div><div><strong>{fmt(day.coverage,0)}<small>%</small></strong><span>Покритие на показателите</span><meter min="0" max="100" value={day.coverage} aria-label="Покритие на показателите"/></div><div><strong>{fmt(day.trend_3d)}</strong><span>Средно за 3 дни · общи показатели</span></div></div>
      {day.assessment_quality==="PARTIAL"&&<p className="stress-inline-note">Оценката е частична: нужни са поне 40% покритие и два компонента. Липсващите измервания не означават нисък стрес.</p>}
      {day.mix_changed&&<p className="stress-inline-note">Съставът на данните е променен.{day.comparison_previous?.delta!=null?` Промяна по същите показатели: ${day.comparison_previous.delta>0?"+":""}${fmt(day.comparison_previous.delta)} точки.`:" Няма достатъчно общи показатели за сравнение с предишния ден."}</p>}
      {day.body_observations?.context_status==="REVIEW_LAB_REFERENCE"&&<p className="body-observation-notice">Има лабораторно отклонение за този ден. Вижте „Тегло и изследвания“; общата оценка не отменя сигнала.</p>}
    </section>
    <section className="response-card"><div className="response-heading"><div><p className="eyebrow">Обща оценка и отделни компоненти</p><h2>Тренд на стреса</h2></div><label className="stress-date">Ден за подробности<select value={selected} onChange={e=>onSelect(Number(e.target.value))}>{history.days.map((d,i)=><option key={d.day} value={i}>{d.day} · {fmt(d.coverage,0)}% покритие</option>)}</select></label></div>
      <div className="stress-channel-buttons">{day.groups.map(g=><button type="button" key={g.key} aria-pressed={visible.includes(g.key)} style={{"--channel-color":colors[g.key]} as React.CSSProperties} onClick={()=>setVisible(visible.includes(g.key)?visible.filter(v=>v!==g.key):[...visible,g.key])}><span>{GROUP_LABELS[g.key]}</span><strong>{fmt(g.score)}</strong><small>{g.weight*100}% базово тегло</small></button>)}</div>
      {history.trainability_unavailable&&<p className="stress-inline-note">ТИ временно не е достъпен. Оценката използва останалите налични показатели.</p>}
      <div className="response-legend"><span>● Обща оценка</span><span style={{color:colors.trend}}>┄ Средно за 3 дни</span>{visible.map(g=><span key={g} style={{color:colors[g]}}>● {GROUP_LABELS[g]}</span>)}{indicator&&<span style={{color:color(indicator)}}>● {CHANNEL_LABELS[indicator]}</span>}</div>
      <MetricChart title="Тренд на стреса по дни. Изберете ден от полето за подробности." unit="Условни точки · 0–100" domain={[0,100]} controls={false} selectedIndex={selected} onSelect={onSelect} maxGap={86400000} series={series.map(key => ({ key, label: key === "total" ? "Обща оценка" : key === "trend" ? "Средно за 3 дни" : GROUP_LABELS[key as Group] ?? CHANNEL_LABELS[key], color: color(key), dashed: key === "trend", points: history.days.map((d,i) => ({ x: Date.parse(d.day), y: value(i,key), breakBefore: key === "total" && d.mix_changed, partial: key === "total" && d.assessment_quality === "PARTIAL" })) }))} />
      <p>Натиснете компонент за неговата динамика. Празните точки са частични оценки; прекъсванията показват липси или промяна в състава на общата оценка.</p>
      <details className="stress-breakdown"><summary>Показатели и принос · {dateLabel(day.day)}</summary>
        <label className="stress-indicator">Отделен показател в графиката<select value={indicator} onChange={e=>setIndicator(e.target.value)}><option value="">Без допълнителен показател</option>{GROUPS.map(g=><optgroup key={g} label={GROUP_LABELS[g]}>{day.channels.filter(c=>c.group===g).map(c=><option key={c.key} value={c.key}>{CHANNEL_LABELS[c.key]}</option>)}</optgroup>)}</select></label>
        <div className="response-table"><table><thead><tr><th>Компонент</th><th>Оценка</th><th>Базово тегло</th><th>Дял днес</th><th>Принос, т.</th></tr></thead><tbody>{day.groups.map(g=><tr key={g.key}><th>{GROUP_LABELS[g.key]}</th><td>{fmt(g.score)}</td><td>{fmt(g.weight*100,0)}%</td><td>{fmt(g.effective_weight*100)}%</td><td>{fmt(g.contribution)}</td></tr>)}</tbody></table></div>
        {GROUPS.map(g=><details key={g}><summary>{GROUP_LABELS[g]} · показатели</summary><div className="response-table"><table><thead><tr><th>Показател</th><th>Измерване</th><th>Оценка / 100</th><th>Тегло</th><th>Основание</th></tr></thead><tbody>{day.channels.filter(c=>c.group===g).map(c=><tr key={c.key}><th><button className="stress-text-button" type="button" onClick={()=>setIndicator(c.key)}>{CHANNEL_LABELS[c.key]}</button></th><td>{fmt(c.raw,c.unit==="ratio"||c.unit==="TI"?4:1)} {units[c.unit??""]??c.unit}</td><td>{fmt(c.score)}</td><td>{fmt(c.weight*100,0)}%</td><td>{c.status==="MISSING"?"Няма съпоставимо измерване":c.status==="NEEDS_BASELINE"?"Натрупваме лична база":c.baseline?`${c.baseline.count} предишни наблюдения`:sources[c.source??""]||"Работна скала"}</td></tr>)}</tbody></table></div></details>)}
        <p>По-високо означава по-неблагоприятна реакция по работната скала. При личните сравнения 50 точки е базовото ниво. Това са условни точки, а не процент физиологичен стрес.</p>
      </details>
    </section>
  </>;
}
