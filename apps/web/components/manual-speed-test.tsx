"use client";
import {useRef,useState,type FormEvent} from "react";
import {useRouter} from "next/navigation";
import {saveModel,type SpeedModel,type SpeedTest} from "../lib/models";
import {manualClockTime,parseManualClock,speedNumber} from "../lib/speed-tests";

export function ManualSpeedTestEditor({model,test,onReset}:{model:SpeedModel;test?:SpeedTest;onReset:()=>void}) {
  const router=useRouter(),p=test?.payload;
  const testId=useRef<string|null>(p?.test_id??null);
  const [name,setName]=useState(p?.name??"");
  const [day,setDay]=useState(p?.day??model.test_window?.end??"");
  const [durationText,setDurationText]=useState(p?manualClockTime(p.duration_s):"");
  const [basis,setBasis]=useState<"DISTANCE"|"SPEED">(p?.measurement_input??"DISTANCE");
  const [measurement,setMeasurement]=useState(p?String(p.measurement_input==="SPEED"?p.speed_kmh:p.distance_m):"");
  const [conditions,setConditions]=useState(p?.conditions??"");
  const [maximal,setMaximal]=useState(Boolean(p?.maximal));
  const [comparable,setComparable]=useState(Boolean(p?.comparable));
  const [flat,setFlat]=useState(Boolean(p?.flat_terrain));
  const [useCS,setUseCS]=useState(Boolean(p?.use_for_cs));
  const [saving,setSaving]=useState(false),[error,setError]=useState("");
  const [saved,setSaved]=useState<{key:string;revision:number}|null>(null);
  const arrived=Boolean(saved&&model.tests.some(t=>t.entry_key===saved.key&&t.revision>=saved.revision));
  const waiting=Boolean(saved&&!arrived),busy=saving||waiting;
  const duration=parseManualClock(durationText),value=Number(measurement.replace(",","."));
  const speed=duration&&value>0?(basis==="DISTANCE"?value/duration*3.6:value):null;
  const distance=duration&&speed?speed*duration/3.6:null;
  const valid=Boolean(duration&&speed&&Number.isFinite(speed)&&speed<=150&&distance&&distance<=1000000&&name.trim()&&day&&conditions.trim().length>=3&&maximal&&comparable&&flat);
  const csEligible=duration!==null&&duration>=120&&duration<=1200;

  async function save(event:FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if(!valid||duration===null||busy)return;
    testId.current??=crypto.randomUUID();
    setSaving(true);setError("");
    try {
      const result=await saveModel("speed-test-manual",{test_id:testId.current,name:name.trim(),day,sport:model.sport,duration_s:duration,
        ...(basis==="DISTANCE"?{distance_m:value}:{speed_kmh:value}),conditions:conditions.trim(),
        maximal:true,comparable:true,flat_terrain:true,use_for_cs:useCS&&csEligible,enabled:true,
        expected_revision:saved?.revision??test?.revision??0});
      setSaved({key:result.entry_key??`manual_${testId.current.replaceAll("-","")}`,revision:result.revision});
      router.refresh();
    }catch(e){setError(e instanceof Error?e.message:"Записването не завърши.");}
    finally{setSaving(false);}
  }

  return <div className="speed-test-editor" aria-busy={busy}>
    <h3>{test?"Редактирай ръчния тест":"Добави ръчен тест"} · {model.sport}</h3>
    <p>Въведи резултат от максимален тест на равен терен, без спирания, от последните 90 дни. Средната скорост се изчислява от дистанцията и времето или се въвежда директно.</p>
    <form className="model-test-form" onSubmit={save}>
      <fieldset disabled={busy}>
        <legend>Резултат от теста</legend>
        <label>Име на теста<input value={name} onChange={e=>setName(e.target.value)} required maxLength={120} placeholder="Напр. 600 м контролно"/></label>
        <div className="model-controls">
          <label>Дата на теста<input type="date" value={day} min={model.test_window?.start} max={model.test_window?.end} onChange={e=>setDay(e.target.value)} required/></label>
          <label>Продължителност, мин:сек<input value={durationText} onChange={e=>setDurationText(e.target.value)} placeholder="Напр. 2:15,5" required aria-describedby="manual-time-help"/></label>
        </div>
        <p id="manual-time-help" className="muted-copy">От 0:10,8 до 725:16. Допуска се и час:мин:сек, с до три знака след десетичната запетая.</p>
        {durationText&&duration===null&&<p role="alert">Въведи валидна продължителност в този диапазон.</p>}
        <div className="model-controls">
          <label>Въвеждам<select value={basis} onChange={e=>{setBasis(e.target.value as "DISTANCE"|"SPEED");setMeasurement("");}}><option value="DISTANCE">Дистанция, метри</option><option value="SPEED">Средна скорост, км/ч</option></select></label>
          <label>{basis==="DISTANCE"?"Дистанция, м":"Средна скорост, км/ч"}<input type="number" inputMode="decimal" min="0.001" max={basis==="DISTANCE"?1000000:150} step="any" value={measurement} onChange={e=>setMeasurement(e.target.value)} required/></label>
        </div>
        {speed!==null&&distance!==null&&Number.isFinite(speed)&&<p role="status">Средна скорост: <strong>{speedNumber(speed)} км/ч</strong> · дистанция: <strong>{speedNumber(distance)} м</strong></p>}
        {speed!==null&&speed>150&&<p role="alert">Провери мерните единици — скоростта е над 150 км/ч.</p>}
        <label>Условия и съпоставимост<input value={conditions} onChange={e=>setConditions(e.target.value)} required minLength={3} maxLength={400} placeholder="Напр. равна писта, без вятър; същите ролки и техника"/></label>
        <label><input type="checkbox" checked={maximal} onChange={e=>setMaximal(e.target.checked)} required/> Максимално непрекъснато усилие, без спирания</label>
        <label><input type="checkbox" checked={flat} onChange={e=>setFlat(e.target.checked)} required/> Тестът е проведен на равен терен</label>
        <label><input type="checkbox" checked={comparable} onChange={e=>setComparable(e.target.checked)} required/> Условията са съпоставими с останалите тестове; при първи тест ги описах по-горе</label>
        <label><input type="checkbox" checked={useCS&&csEligible} disabled={!csEligible} onChange={e=>setUseCS(e.target.checked)}/> Използвай и за критична скорост · само от 2 до 20 минути</label>
      </fieldset>
      <p className="muted-copy">Ръчният резултат участва в кривата като скорост на равен терен. Не се прилага допълнителна корекция за наклон. Записът служи за калибрация и не добавя тренировъчно натоварване.</p>
      <div className="model-controls"><button className="action-button" disabled={!valid||busy}>{saving?"Записваме…":waiting?"Обновяваме кривата…":"Запази теста и обнови кривата"}</button>
        {(test||saved)&&<button className="action-button secondary" type="button" disabled={busy} onClick={onReset}>{saved?"Добави още един тест":"Откажи редакцията"}</button>}</div>
    </form>
    {error&&<p role="alert">{error}</p>}
    {saved&&<p role="status">{arrived?"Тестът е запазен и моделът е обновен.":"Тестът е запазен. Изчакваме обновяването на модела."}</p>}
    {waiting&&<button type="button" className="action-button secondary" onClick={()=>router.refresh()}>Обнови модела</button>}
  </div>;
}
