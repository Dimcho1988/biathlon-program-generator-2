"use client";
import { useState } from "react";
import { durationHms } from "../lib/duration-format";
import { defaultLoadProgression, type ManagementProfile, type TrainingReturn } from "../lib/training-management";

const zones = ["Z1","Z2","Z3","Z4","Z5"] as const;
function HistoricalTime({zone, value, onChange}: {zone: string; value: number; onChange: (n:number)=>void}) {
  const [text,setText] = useState(()=>durationHms(value));
  return <label>{zone} · исторически Q, ч:мм:сс<input required type="text" inputMode="text" placeholder="10:00:00" pattern="[0-9]{1,3}:[0-5][0-9](:[0-5][0-9])?" value={text} onChange={e=>{
    const t=e.target.value; setText(t);
    const valid=/^\d{1,3}:[0-5]\d(?::[0-5]\d)?$/.test(t), parts=t.split(":").map(Number);
    onChange(valid ? parts[0]*60+parts[1]+(parts[2]??0)/60 : NaN);
  }}/></label>;
}

export function TrainingReturnEditor({profile,onChange,today}: {profile:ManagementProfile;onChange:(p:ManagementProfile)=>void;today:string}) {
  const r=profile.training_return;
  const update=(patch:Partial<TrainingReturn>)=>onChange({...profile,training_return:{...r!,...patch}});
  const manual=!!r&&Object.keys(r.historical_weekly_q).length>0;
  return <section aria-label="Връщане след пауза">
    <h3>Връщане след пауза</h3>
    <label>Как е протекла подготовката?<select value={r?.mode??"NONE"} onChange={e=>{
      const mode=e.target.value;
      onChange({...profile,...(mode==="CONFIRMED_BREAK"?{load_progression:{...(profile.load_progression??defaultLoadProgression()),enabled:true}}:{}),
        training_return:mode==="NONE"?null:mode==="CONTINUED"?{mode,historical_weekly_q:{}}:
          {mode:"CONFIRMED_BREAK",pause_start:r?.pause_start??null,resume_date:r?.resume_date??today,historical_weekly_q:r?.historical_weekly_q??{}}});
    }}>
      <option value="NONE">Без заявена пауза</option><option value="CONFIRMED_BREAK">Потвърждавам пълна пауза без тренировки</option><option value="CONTINUED">Тренирах, но липсват записи</option>
    </select></label>
    {r?.mode==="CONTINUED"&&<p>Липсващите записи не се броят за почивка. Добави изпълнените тренировки с приблизителните им времена и зони, за да се оцени текущото натоварване.</p>}
    {r?.mode==="CONFIRMED_BREAK"&&<>
      <div className="management-form-grid"><label>Първи ден без тренировки<input required type="date" value={r.pause_start??""} max={r.resume_date??undefined} onChange={e=>update({pause_start:e.target.value})}/></label><label>Първи ден на подновената подготовка<input required type="date" value={r.resume_date??""} min={r.pause_start??undefined} onChange={e=>update({resume_date:e.target.value})}/></label></div>
      <p>Линейно връщане за 1,3 × дните на паузата, до 60 дни пауза. Срокът се закръгля нагоре до цели седмици, най-малко две. Началната база по зона е по-голямото от 90% от долния ориентир и 50% от устойчивата история, до целта за връщане.</p>
      <p>Целта е 100% от устойчивия Q преди паузата; при пауза над 60 дни — 90%. След изпълненото връщане продължава обичайният прираст. Срокът може да се удължи при неизпълнение, ограничено време или възстановяване.</p>
      <label className="management-check"><input type="checkbox" checked={manual} onChange={e=>update({historical_weekly_q:e.target.checked?{Z1:0,Z2:0,Z3:0,Z4:0,Z5:0}:{}})}/>Въвеждам устойчива история преди паузата</label>
      {manual?<><p>Среден седмичен приравнен обем Q по зони, без преливането. Въведи всички зони; 0:00 за неизползвана зона. Това е заявена опора, отделна от измерените тренировки.</p><div className="management-form-grid">{zones.map(z=><HistoricalTime key={z} zone={z} value={r.historical_weekly_q[z]??0} onChange={v=>update({historical_weekly_q:{...r.historical_weekly_q,[z]:v}})}/>)}</div></>:<p>Историята се търси в завършените мезоцикли преди паузата и в запазената опора. При липса се използват 90% от долните експертни граници. По-ниска или нулева лична история се запазва.</p>}
    </>}
  </section>;
}
