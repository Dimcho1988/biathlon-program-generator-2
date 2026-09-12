"use client";
import Link from "next/link";
import {useEffect, useState, type FormEvent, type MouseEvent} from "react";
import {useRouter} from "next/navigation";
import {saveModel, type SpeedModel} from "../lib/models";
import {clockTime, parseClock, parseSpeedPreview, speedNumber as n, testActivities, type SpeedPreview} from "../lib/speed-tests";

export function SpeedTestEditor({model, canEdit, activityRef}:{model:SpeedModel;canEdit:boolean;activityRef?:string}) {
  const [search,setSearch]=useState("");
  const [selected,setSelected]=useState(model.activities.some(a=>a.activity_ref===activityRef&&a.sport===model.sport)?activityRef!:"");
  const options=testActivities(model.activities,model.sport,search);
  const chosen=model.activities.find(a=>a.activity_ref===selected);
  return <div className="speed-test-editor">
    <h3>1. Избери активност</h3>
    <p>Само {model.sport} · последните 90 дни · най-новите са първи. Можеш да започнеш и от страницата на конкретна активност.</p>
    <div className="model-controls"><label>Търси по име или дата<input type="search" value={search} onChange={e=>setSearch(e.target.value)} placeholder="Напр. 2026-09-10"/></label>
      <label className="speed-activity-choice">Активност<select value={selected} onChange={e=>setSelected(e.target.value)}>
        <option value="">Избери активност за преглед</option>
        {chosen&&!options.some(a=>a.activity_ref===selected)&&<option value={selected}>{chosen.day} · {chosen.name}</option>}
        {options.map(a=><option value={a.activity_ref} key={a.activity_ref}>{a.day} · {a.name}{a.elapsed_s?` · ${clockTime(a.elapsed_s)}`:""}</option>)}
      </select></label></div>
    {!options.length&&<p role="status">Няма активности, които съвпадат с търсенето за {model.sport}.</p>}
    {selected&&<SpeedSegmentEditor key={selected} activityRef={selected} model={model} canEdit={canEdit}/>}
  </div>;
}

function SpeedSegmentEditor({activityRef,model,canEdit}:{activityRef:string;model:SpeedModel;canEdit:boolean}) {
  const router=useRouter();
  const [preview,setPreview]=useState<SpeedPreview|null>(null);
  const [request,setRequest]=useState<{start?:number;duration?:number;attempt:number}>({attempt:0});
  const [loading,setLoading]=useState(true),[error,setError]=useState("");
  const [startText,setStartText]=useState("0:00"),[endText,setEndText]=useState("");
  const [handle,setHandle]=useState<"start"|"end">("start");
  const [maximal,setMaximal]=useState(false),[comparable,setComparable]=useState(false),[useCS,setUseCS]=useState(false);
  const [saving,setSaving]=useState(false),[saveError,setSaveError]=useState("");
  const [saved,setSaved]=useState<{revision:number;start:number;duration:number}|null>(null);
  const arrived=saved&&model.tests.some(t=>t.payload.activity_ref===activityRef&&t.payload.start_s===saved.start&&t.payload.duration_s===saved.duration&&t.revision>=saved.revision);
  const waiting=Boolean(saved&&!arrived),busy=saving||waiting;
  const start=parseClock(startText),end=parseClock(endText),duration=start!==null&&end!==null?end-start:null;
  const valid=start!==null&&start<=172800&&end!==null&&duration!==null&&duration>=11&&duration<=43516&&Boolean(preview&&end<=preview.elapsed_s);
  const selection=preview?.selection;
  const fresh=Boolean(valid&&selection&&selection.start_s===start&&selection.duration_s===duration);
  const ready=fresh&&selection?.eligible&&!loading&&!error;

  useEffect(()=>{
    const controller=new AbortController();
    const params=new URLSearchParams({activity_ref:activityRef});
    if(request.start!==undefined&&request.duration!==undefined){params.set("start_s",String(request.start));params.set("duration_s",String(request.duration));}
    fetch(`/api/athlete/models/speed-preview?${params}`,{cache:"no-store",signal:controller.signal})
      .then(async response=>{const body=await response.json();if(!response.ok)throw new Error(body.error||"Прегледът не е достъпен.");return parseSpeedPreview(body);})
      .then(data=>{if(controller.signal.aborted)return;setPreview(data);setEndText(old=>old||clockTime(Math.min(720,data.elapsed_s)));})
      .catch(e=>{if(!controller.signal.aborted)setError(e instanceof Error?e.message:"Прегледът не е достъпен.");})
      .finally(()=>{if(!controller.signal.aborted)setLoading(false);});
    return ()=>controller.abort();
  },[activityRef,request]);

  function bounds(which:"start"|"end",text:string) {
    if(which==="start")setStartText(text);else setEndText(text);
    setMaximal(false);setComparable(false);setSaveError("");setSaved(null);
  }
  function check() {
    if(!valid||start===null||duration===null)return;
    setLoading(true);setError("");setSaveError("");setSaved(null);
    setRequest(old=>({start,duration,attempt:old.attempt+1}));
  }
  async function save(event:FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if(!ready||!preview?.source_run_key||start===null||duration===null||!maximal||!comparable)return;
    const form=new FormData(event.currentTarget);
    const previous=model.tests.find(t=>t.payload.activity_ref===activityRef&&t.payload.start_s===start&&t.payload.duration_s===duration);
    setSaving(true);setSaveError("");setSaved(null);
    try {
      const result=await saveModel("speed-test",{activity_ref:activityRef,start_s:start,duration_s:duration,maximal,comparable,
        enabled:true,use_for_cs:useCS&&duration>=120&&duration<=1200,conditions:String(form.get("conditions")).trim(),
        expected_revision:previous?.revision||0,expected_source_run_key:preview.source_run_key});
      setSaved({revision:result.revision,start,duration});router.refresh();
    } catch(e){setSaveError(e instanceof Error?e.message:"Записването не завърши.");}
    finally{setSaving(false);}
  }

  const vmax=Math.max(5,Math.ceil(Math.max(0,...(preview?.series.map(p=>p.speed_kmh||0)||[]))/5)*5);
  const x=(t:number)=>48+824*t/(preview?.elapsed_s||1),y=(v:number)=>200-160*v/vmax;
  const curve=preview?.series.map((p,i)=>p.speed_kmh===null?"":`${i&&preview.series[i-1].speed_kmh!==null?"L":"M"}${x(p.elapsed_s)},${y(p.speed_kmh)}`).join(" ");
  function pick(event:MouseEvent<SVGSVGElement>){
    if(!preview||busy)return;
    const box=event.currentTarget.getBoundingClientRect();
    const seconds=Math.round(Math.max(0,Math.min(1,((event.clientX-box.left)*920/box.width-48)/824))*preview.elapsed_s);
    bounds(handle,clockTime(seconds));
  }

  return <div className="speed-segment-editor" aria-busy={loading||busy}>
    <p><Link href={`/activities/${activityRef}`}>Отвори цялата активност</Link></p>
    {error&&<p role="alert">{error}</p>}
    {loading&&<p role="status">Проверяваме подробните данни за скоростта…</p>}
    {!preview&&error&&<button type="button" className="action-button secondary" onClick={()=>{setLoading(true);setError("");setRequest(r=>({...r,attempt:r.attempt+1}));}}>Опитай отново</button>}
    {preview&&<>
      <h3>2. Избери непрекъснат участък</h3>
      <p>Запис: {clockTime(preview.elapsed_s)} · време от началото, включително паузите. Изключи загряването и разпускането.</p>
      {preview.status==="READY"&&preview.uses_legacy_samples&&<p>За този запис е наличен по-стар анализ. Ако покритието не достига, обнови анализите от <Link href="/">началния екран</Link> и провери отново.</p>}
      {preview.status==="ANALYSIS_REQUIRED"?<p role="alert">Липсват подробни данни за скоростта. Отвори <Link href="/">началния екран</Link> → „Обнови данните“ и обнови анализите на активностите.</p>:preview.status==="OUTSIDE_TEST_WINDOW"?<p role="alert">Тази активност е извън последните 90 дни. Избери по-скорошен тест.</p>:<>
        <div className="model-controls" role="group" aria-label="Избор върху графиката"><button type="button" className="action-button secondary" aria-pressed={handle==="start"} disabled={busy} onClick={()=>setHandle("start")}>Постави начало</button><button type="button" className="action-button secondary" aria-pressed={handle==="end"} disabled={busy} onClick={()=>setHandle("end")}>Постави край</button><span>Натисни върху графиката или използвай полетата и плъзгачите.</span></div>
        <figure className="history-chart speed-segment-chart"><svg viewBox="0 0 920 242" role="img" aria-label="Скорост на активността и избран тестов участък" onClick={pick}>
          {[0,.5,1].map(f=><g key={f}><line x1="48" x2="872" y1={y(vmax*f)} y2={y(vmax*f)} stroke="currentColor" opacity=".12"/><text x="40" y={y(vmax*f)+4} textAnchor="end" fill="currentColor" fontSize="12">{n(vmax*f)}</text></g>)}
          {preview.series.filter(p=>p.eligible_fraction<.98).map(p=><rect key={p.elapsed_s} x={Math.max(48,x(p.elapsed_s)-824/preview.series.length/2)} y="35" width={824/preview.series.length} height="170" fill="currentColor" opacity=".09"/>)}
          {start!==null&&end!==null&&end>start&&<rect x={x(Math.min(start,preview.elapsed_s))} y="35" width={Math.max(0,x(Math.min(end,preview.elapsed_s))-x(Math.min(start,preview.elapsed_s)))} height="170" fill="var(--accent)" opacity=".16"/>}
          <path d={curve} fill="none" stroke="var(--accent)" strokeWidth="2"/>
          {start!==null&&start<=preview.elapsed_s&&<line x1={x(start)} x2={x(start)} y1="35" y2="205" stroke="var(--accent)" strokeWidth="2"/>}
          {end!==null&&end<=preview.elapsed_s&&<line x1={x(end)} x2={x(end)} y1="35" y2="205" stroke="var(--accent)" strokeWidth="2"/>}
          {[0,.25,.5,.75,1].map(f=><text key={f} x={x(preview.elapsed_s*f)} y="228" textAnchor="middle" fill="currentColor" fontSize="12">{clockTime(preview.elapsed_s*f)}</text>)}
          <text x="48" y="20" fill="currentColor" fontSize="12">Vflat · км/ч</text>
        </svg><figcaption>Оцветеният участък е твоят избор. Сивите ивици показват непълно покритие или изключени данни. Изчислението използва подробния запис.</figcaption></figure>
        <div className="model-controls"><label>Начало, мин:сек<input value={startText} disabled={busy} onChange={e=>bounds("start",e.target.value)} placeholder="0:00" aria-describedby="speed-time-format"/></label><label>Край, мин:сек<input value={endText} disabled={busy} onChange={e=>bounds("end",e.target.value)} placeholder="12:00" aria-describedby="speed-time-format"/></label><strong>Участък: {duration!==null&&duration>0?clockTime(duration):"—"}</strong></div>
        <p id="speed-time-format" className="muted-copy">Например 12:30 означава 12 минути и 30 секунди. Допуска се и час:мин:сек.</p>
        <div className="speed-range-controls"><label>Начало на участъка<input type="range" min="0" max={preview.elapsed_s} step="1" value={Math.min(start??0,preview.elapsed_s)} disabled={busy} onChange={e=>bounds("start",clockTime(Number(e.target.value)))}/></label><label>Край на участъка<input type="range" min="0" max={preview.elapsed_s} step="1" value={Math.min(end??0,preview.elapsed_s)} disabled={busy} onChange={e=>bounds("end",clockTime(Number(e.target.value)))}/></label></div>
        {!valid&&<p role="alert">Задай начало и край в рамките на записа. Продължителността трябва да е от 0:11 до 725:16.</p>}
        <button type="button" className="action-button secondary" disabled={!valid||loading||busy} onClick={check}>Провери участъка без запис</button>
        {selection&&!fresh&&<p role="status">Участъкът е променен. Провери го отново преди запис.</p>}
        {fresh&&selection&&<section className="speed-selection-result" aria-label="Проверка на избрания участък">
          <h3>3. Провери резултата</h3><dl className="model-prediction"><div><dt>Продължителност</dt><dd>{clockTime(selection.duration_s)}</dd></div><div><dt>Подходящи данни</dt><dd>{n(selection.coverage_percent)}%</dd></div><div><dt>Средна Vflat скорост</dt><dd>{selection.speed_kmh===null?"—":`${n(selection.speed_kmh)} км/ч`}</dd></div><div><dt>Еквивалентна дистанция</dt><dd>{selection.distance_m===null?"—":`${n(selection.distance_m/1000)} км`}</dd></div></dl>
          {selection.eligible?<p>Покритието е достатъчно. Само ти можеш да потвърдиш дали това е било максимално усилие и при какви условия.</p>:<><p role="alert">{selection.status==="OUTSIDE_ACTIVITY"?"Краят е извън наличния скоростен запис. Измести го навътре в графиката.":"Не достигат необходимите 98% подходящи данни. Избери непрекъснат участък с по-добро покритие."}</p><p>Спускане под −3%: {clockTime(selection.excluded_seconds.downhill)} · други изключени данни: {clockTime(selection.excluded_seconds.invalid)} · липсващ запис или паузи: {clockTime(selection.excluded_seconds.missing_or_paused)}.</p></>}
          <p className="muted-copy">Прегледът не записва тест и не променя индивидуалната крива.</p>
        </section>}
        {canEdit&&<form onSubmit={save} className="model-test-form"><fieldset disabled={!ready||busy}>
          <legend>Потвърди максималния тест</legend>
          <label>Условия и съпоставимост<input name="conditions" minLength={3} maxLength={400} required placeholder="Напр. същите ролки и техника, сух асфалт, без спирания"/></label>
          <label><input type="checkbox" checked={maximal} onChange={e=>setMaximal(e.target.checked)} required/> Това е максимално непрекъснато усилие</label>
          <label><input type="checkbox" checked={comparable} onChange={e=>setComparable(e.target.checked)} required/> Условията са съпоставими с другите тестове; при първи тест ги описах по-горе</label>
          <label><input type="checkbox" checked={useCS&&Boolean(duration&&duration>=120&&duration<=1200)} disabled={!duration||duration<120||duration>1200} onChange={e=>setUseCS(e.target.checked)}/> Използвай и за критична скорост · само от 2 до 20 минути</label>
        </fieldset><button className="action-button" disabled={!ready||!maximal||!comparable||busy||!preview.source_run_key}>{saving?"Записваме…":waiting?"Обновяваме кривата…":"Запази теста и обнови кривата"}</button></form>}
      </>}
    </>}
    {saveError&&<p role="alert">{saveError}</p>}
    {saved&&<p role="status">{arrived?"Тестът е запазен и моделът е обновен.":"Тестът е запазен. Изчакваме обновяването на модела."}</p>}
    {waiting&&<button className="action-button secondary" type="button" onClick={()=>router.refresh()}>Обнови модела</button>}
  </div>;
}
