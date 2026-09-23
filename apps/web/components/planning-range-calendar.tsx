"use client";
import { useState } from "react";
import type { PlanningCalendarEvent } from "../lib/planning-calendar";

const iso = (d: Date) => d.toISOString().slice(0,10);
export function PlanningRangeCalendar({today, events, onSelect}: {today: string; events: PlanningCalendarEvent[]; onSelect: (start: string,end: string)=>void}) {
  const [month,setMonth]=useState(today.slice(0,7));
  const [start,setStart]=useState<string|null>(null),[end,setEnd]=useState<string|null>(null);
  const first=new Date(`${month}-01T12:00:00Z`);
  const count=new Date(Date.UTC(first.getUTCFullYear(),first.getUTCMonth()+1,0,12)).getUTCDate();
  const offset=(first.getUTCDay()+6)%7;
  const dates=Array.from({length:Math.ceil((count+offset)/7)*7},(_,i)=>i>=offset&&i<offset+count?`${month}-${String(i-offset+1).padStart(2,"0")}`:null);
  function move(delta:number){const d=new Date(first);d.setUTCMonth(d.getUTCMonth()+delta);setMonth(iso(d).slice(0,7));}
  function choose(day:string){
    if(!start||end){setStart(day);setEnd(null);}
    else {setStart(day<start?day:start);setEnd(day<start?start:day);}
  }
  return <section aria-label="Избор на период в календара">
    <p>Избери начало и край в календара, после добави старт, лагер или недостъпен период. За един ден избери датата два пъти.</p>
    <div className="calendar-actions"><button type="button" className="action-button secondary" onClick={()=>move(-1)} aria-label="Предишен месец">←</button><strong>{first.toLocaleDateString("bg-BG",{month:"long",year:"numeric",timeZone:"UTC"})}</strong><button type="button" className="action-button secondary" onClick={()=>move(1)} aria-label="Следващ месец">→</button></div>
    <table style={{tableLayout:"fixed",width:"100%",maxWidth:650}}><thead><tr>{["Пн","Вт","Ср","Чт","Пт","Сб","Нд"].map(d=><th key={d}>{d}</th>)}</tr></thead><tbody>{Array.from({length:dates.length/7},(_,week)=><tr key={week}>{dates.slice(week*7,week*7+7).map((day,i)=>{
      const selected=!!day&&!!start&&start<=day&&day<=(end??start);
      const items=day?events.filter(e=>e.start_date<=day&&day<=e.end_date):[];
      return <td key={day??i} style={{padding:2}}>{day&&<button type="button" aria-label={day+(items.length?": "+items.map(e=>e.name||e.event_type).join(", "):"")} aria-pressed={selected} onClick={()=>choose(day)} style={{width:"100%",minHeight:42,borderRadius:8,border:selected?"2px solid #176b62":"1px solid #cedbd8",background:selected?"#d8eee8":"transparent",color:"inherit",cursor:"pointer"}}>{Number(day.slice(8))}{items.length>0&&<span aria-hidden="true"> ·</span>}</button>}</td>;
    })}</tr>)}</tbody></table>
    <p aria-live="polite">{start?end?`${start} – ${end}`:`Начало: ${start}. Избери край.`:"Няма избран период."}</p>
    <button type="button" className="action-button secondary" disabled={!start||!end||events.length>=100} onClick={()=>{if(start&&end){onSelect(start,end);setStart(null);setEnd(null);}}}>Добави събитие за периода</button>
  </section>;
}
