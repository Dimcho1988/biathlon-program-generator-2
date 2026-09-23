"use client";
import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { FIELD_LABELS, GROUPS, GROUP_LABELS, PHASES, STATES, type ResponseHistory, type ResponseDay, type Session } from "../lib/response-monitoring";

const colors = {total:"var(--response-total-ink, #112839)",subjective:"#087f8c",rpe:"#bd6800",physiology:"#7658aa"};
const fmt = (v:number|null|undefined) => v==null?"—":v.toLocaleString("bg-BG",{maximumFractionDigits:1});
const labelDate = (s:string) => s.slice(8,10)+"."+s.slice(5,7);
const anchorLabels:Record<string,string[]> = {
  sleep_quality:["Много добър","Добър","Среден","Лош","Много лош"],fatigue:["Без умора","Лека","Умерена","Силна","Много силна"],
  soreness:["Няма","Лека","Умерена","Силна","Много силна"],stress:["Много нисък","Нисък","Умерен","Висок","Много висок"],
  motivation:["Много мотивиран","Мотивиран","Средно","Слабо мотивиран","Без мотивация"]};

function SaveForm({kind,children,build,disabled=false}:{kind:string;children:React.ReactNode;build:(f:FormData)=>unknown;disabled?:boolean}) {
  const router = useRouter();
  const [busy,setBusy] = useState(false), [message,setMessage] = useState("");
  async function submit(e:FormEvent<HTMLFormElement>) {
    e.preventDefault(); if(busy || disabled)return;
    const form = new FormData(e.currentTarget); setBusy(true);setMessage("");
    try {
      const r=await fetch("/api/athlete/response",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({kind,payload:build(form)})});
      const result=await r.json();
      if(!r.ok) throw new Error(result.error||"Записването не успя.");
      setMessage("Запазено успешно."); router.refresh();
    } catch(error){setMessage(error instanceof Error?error.message:"Опитайте отново.");}
    finally{setBusy(false);}
  }
  return <form onSubmit={submit} className="response-form"><fieldset disabled={disabled||busy}>{children}<button className="action-button" disabled={busy||disabled} type="submit">{busy?"Записваме…":"Запази"}</button></fieldset><p role="status" aria-live="polite">{message}</p></form>;
}

function DailyForm({day,today,canReport}:{day:ResponseDay;today:string;canReport:boolean}) {
  const report=day.daily_report;
  return <SaveForm key={`${day.day}:${day.daily_revision}`} kind="daily" disabled={!canReport} build={f=>({
    day:day.day,observed_at:report?.observed_at??(day.day===today?new Date().toISOString():null),
    ...Object.fromEntries(Object.keys(FIELD_LABELS).map(k=>[k,Number(f.get(k))])),pain_or_illness:f.get("pain_or_illness")==="on",note:String(f.get("note")||""),expected_revision:day.daily_revision})}>
    <legend>Сутрешна оценка · {labelDate(day.day)}</legend>
    <p>Отговорете според състоянието преди тренировката. Всички пет отговора са нужни; няма предварително избрани оценки.</p>
    <div className="response-fields">{Object.entries(FIELD_LABELS).map(([key,label])=><label key={key}>{label}<select name={key} defaultValue={report?String(report[key as keyof typeof report]):""} required><option value="">Избери…</option>{anchorLabels[key].map((text,i)=><option key={i} value={i+1}>{i+1} · {text}</option>)}</select></label>)}</div>
    <label className="response-check"><input type="checkbox" name="pain_or_illness" defaultChecked={report?.pain_or_illness}/>Имам болка или симптоми, които искам да обсъдя с треньора</label>
    <label>Бележка (по желание)<textarea name="note" maxLength={500} defaultValue={report?.note||""}/></label>
    {!canReport&&<p>Личната оценка се въвежда от спортиста.</p>}
  </SaveForm>;
}

function SessionForm({session,canReport}:{session:Session;canReport:boolean}) {
  return <details className="response-session"><summary><span>{labelDate(session.day)} · {session.name}</span><span>RPE {fmt(session.rpe)} · sRPE {fmt(session.srpe_load)}</span></summary>
    <p><Link href={`/activities/${session.activity_ref}`}>Отвори активността</Link> · Източник: {session.source==="ONFLOWS"?"onFlows":session.source==="INTERVALS"?"Intervals":"няма оценка"}</p>
    <SaveForm key={`${session.activity_ref}:${session.revision}`} kind="session" disabled={!canReport} build={f=>({activity_ref:session.activity_ref,rpe:Number(f.get("rpe")),duration_minutes:Number(f.get("duration_minutes")),timing:f.get("timing"),note:String(f.get("note")||""),expected_revision:session.revision})}>
      <legend>Какво усилие изискваше цялата тренировка?</legend>
      <div className="response-fields"><label>Усилие RPE · 0–10<select name="rpe" required defaultValue={session.rpe??""}><option value="">Избери…</option>{Array.from({length:11},(_,i)=><option value={i} key={i}>{i}{i===0?" · без усилие":i===3?" · умерено":i===5?" · тежко":i===10?" · максимално":""}</option>)}</select></label>
      <label>Цяла сесия, минути<input name="duration_minutes" type="number" min="0.1" max="1440" step="0.1" required defaultValue={session.duration_minutes??(session.suggested_duration_minutes?Math.round(session.suggested_duration_minutes*10)/10:"")}/></label>
      <label>Кога направихте оценката?<select name="timing" required defaultValue={session.timing==="UNKNOWN"?"":session.timing}><option value="">Избери…</option><option value="IMMEDIATE">Непосредствено след края</option><option value="DELAYED">Около 10–60 минути след края</option><option value="NEXT_DAY">На следващия ден</option></select></label></div>
      <p>Продължителността включва почивките в тренировката. Проверете предложеното време. sRPE = RPE × минути; не се прибавя към пулсовото натоварване.</p>
      <label>Бележка<textarea name="note" maxLength={500} defaultValue={session.note}/></label>
    </SaveForm>
    <p>{session.expected_rpe==null?`Нужни са поне 3 предишни съпоставими сесии. Налични: ${session.comparable_count}.`:`Обичайно RPE при сходна работа: ${fmt(session.expected_rpe)}; днес: ${fmt(session.rpe)}.`}</p>
  </details>;
}

function DayDetails({day}:{day:ResponseDay}) {
  const r=day.daily_report;
  return <section className="response-card" aria-label="Принос по компоненти"><div className="response-heading"><div><p className="eyebrow">Как се получава оценката</p><h2>{labelDate(day.day)} · {STATES[day.state]||day.state}</h2></div><div className="response-total">{fmt(day.total)}<small>{day.total==null?`Непълни данни · ${day.coverage}% покритие`:`Обща оценка · 100% покритие`}</small></div></div>
    <p>Фаза: {PHASES[day.phase]}. Дневният статус описва субективната реакция спрямо личната база. Общата оценка е в условни точки, не процент физиологичен стрес или възстановяване.</p>
    <div className="response-table"><table><thead><tr><th>Компонент</th><th>Оценка / 100</th><th>Тегло</th><th>Принос</th></tr></thead><tbody>{day.groups.map(g=><tr key={g.key}><td>{GROUP_LABELS[g.key]}</td><td>{fmt(g.score)}</td><td>{g.weight*100}%</td><td>{fmt(g.contribution)}</td></tr>)}</tbody></table></div>
    <div className="response-detail-grid"><div><h3>Субективни оценки</h3>{r?<ul>{Object.entries(FIELD_LABELS).map(([key,label])=><li key={key}>{label}: <strong>{String(r[key as keyof typeof r])}/5</strong> · принос в общия индекс: {fmt((Number(r[key as keyof typeof r])-1)*25*.1)}</li>)}</ul>:<p>Няма анкета от onFlows за този ден.</p>}<p>Средна стойност на петте оценки, преобразувана от 1–5 в 0–100. Всеки въпрос има равен дял в субективния компонент.</p>{day.baseline?<p>Лична база: {fmt(day.baseline.median)} точки от {day.baseline.count} дни. Опорна дата: {day.baseline_anchor}.</p>:<p>За личния диапазон са нужни 14 валидни дни в предходните 28.</p>}</div>
    <div><h3>Пулс и HRV</h3>{Object.entries(day.physiology).map(([key,p])=><p key={key}>{key==="hrv"?"HRV (RMSSD)":"Пулс в покой"}: <strong>{fmt(p.raw)} {key==="hrv"?"ms":"уд./мин"}</strong><br/>{p.baseline?`Лична база от ${p.baseline.count} дни; компонентна оценка ${fmt(p.score)}.`:"Няма достатъчно съпоставима история."}</p>)}<p>Източник: Intervals. SDNN не заменя RMSSD. Необичайно висок HRV също се разглежда като отклонение.</p></div>
    <div><h3>Усилие от предишния ден</h3>{day.rpe_sessions.length?day.rpe_sessions.map(s=><p key={s.activity_ref}>{s.name}: RPE {fmt(s.rpe)}, обичайно {fmt(s.expected_rpe)} · {s.comparable_count} съпоставими сесии.</p>):<p>Няма сесия от предишния ден. Това е липсващ RPE компонент, а не нулев стрес.</p>}<p>Сравняваме същия спорт, сходна продължителност и разпределение по зони, при еднакъв момент на оценяване.</p></div></div>
    <details><summary>Всички налични уелнес данни от Intervals</summary><p>Субективните скали на външния източник се пазят отделно; не ги приравняваме мълчаливо към анкетата 1–5.</p><div className="response-table"><table><tbody>{Object.entries(day.device_metrics).map(([k,v])=><tr key={k}><th>{FIELD_LABELS[k]||k}</th><td>{fmt(v.value)} {v.unit}</td></tr>)}</tbody></table></div></details>
  </section>;
}

export function ResponseMonitoring({history,canReport,canEditPlan}:{history:ResponseHistory;canReport:boolean;canEditPlan:boolean}) {
  const [selected,setSelected]=useState(history.days.length-1);
  const [visible,setVisible]=useState<Record<string,boolean>>({total:true,subjective:true,rpe:true,physiology:true});
  const [showTests,setShowTests]=useState(false);
  const day=history.days[Math.min(selected,history.days.length-1)];
  const x=(i:number)=>45+i*910/Math.max(1,history.days.length-1), y=(value:number)=>220-value*1.8;
  function path(channel:string) {let open=false;return history.days.map((d,i)=>{const v=channel==="total"?d.total:d.groups.find(g=>g.key===channel)?.score??null;if(v===null){open=false;return "";}const cmd=open?"L":"M";open=true;return `${cmd}${x(i)},${y(v)}`;}).join(" ");}
  return <>
    <section className="response-card"><div className="response-heading"><div><p className="eyebrow">Натоварване · реакция · отзвучаване</p><h2>Тренд на стреса</h2></div><span className="response-badge">Наблюдение · пилотна версия</span></div>
      <p>Високата реакция се тълкува според фазата и отзвучаването. Ниската оценка не задейства увеличение на натоварването.</p>
      <div className="response-legend">{["total",...GROUPS].map(k=><label key={k} style={{color:colors[k as keyof typeof colors]}}><input type="checkbox" checked={visible[k]} onChange={e=>setVisible({...visible,[k]:e.target.checked})}/>{k==="total"?"Обща оценка":GROUP_LABELS[k as typeof GROUPS[number]]}</label>)}</div>
      <svg className="response-chart" viewBox="0 0 1000 265" role="img" aria-label="Тренд на стреса по дни. Изберете ден от графиката или полето под нея.">
        {[0,25,50,75,100].map(v=><g key={v}><line x1="45" x2="955" y1={y(v)} y2={y(v)} stroke="#cddce2"/><text x="10" y={y(v)+4}>{v}</text></g>)}
        <line x1={x(selected)} x2={x(selected)} y1="35" y2="225" stroke="#92a6af" strokeDasharray="3 3"/>
        {["total",...GROUPS].filter(k=>visible[k]).map(k=><path key={k} d={path(k)} fill="none" stroke={colors[k as keyof typeof colors]} strokeWidth={k==="total"?3:2}/>)}
        {history.days.map((d,i)=><g key={d.day}>{["total",...GROUPS].filter(k=>visible[k]).map(k=>{const v=k==="total"?d.total:d.groups.find(g=>g.key===k)?.score;return v==null?null:<circle key={k} cx={x(i)} cy={y(v)} r={selected===i?4:2.5} fill={colors[k as keyof typeof colors]}/>;})}<rect x={x(i)-455/Math.max(1,history.days.length-1)} y="30" width={910/Math.max(1,history.days.length-1)} height="195" fill="transparent" onClick={()=>setSelected(i)} style={{cursor:"pointer"}}><title>{`${d.day} · обща оценка ${fmt(d.total)} · покритие ${d.coverage}%`}</title></rect>{(i===0||i===history.days.length-1||i%Math.max(1,Math.ceil(history.days.length/8))===0)&&<text x={x(i)} y="250" textAnchor="middle">{labelDate(d.day)}</text>}</g>)}
      </svg>
      <div className="response-select-day"><label>Ден за подробности<select value={selected} onChange={e=>setSelected(Number(e.target.value))}>{history.days.map((d,i)=><option key={d.day} value={i}>{d.day} · {d.coverage}% покритие</option>)}</select></label><p>Изберете точка, за да видите приноса. Линиите се прекъсват при липсващи данни.</p></div>
    </section>
    {day&&<><DayDetails day={day}/><section className="response-card"><DailyForm day={day} today={history.today} canReport={canReport}/></section></>}
    <section className="response-card"><h2>Възприето натоварване по активности</h2><p>Оценката от часовника се показва, когато Intervals я е предал. Поправката в onFlows има предимство и се пази при следващ импорт.</p>{history.sessions.length?history.sessions.slice().reverse().map(s=><SessionForm key={s.activity_ref} session={s} canReport={canReport}/>):<p>Няма активности в периода.</p>}</section>
    <section className="response-card"><h2>Реакция и отзвучаване по блокове</h2><p>Задайте предварително натоварващата част и края на разтоварването. Опорната база се фиксира при създаването. Започнал блок не се удължава със задна дата.</p><p>Обобщението тук следи субективното състояние. Проверявайте успоредно пулса, HRV и възприетото усилие в графиката. Прекъсване на оценките или ново покачване отменя потвърденото отзвучаване.</p>
      {history.blocks.map(b=><article key={b.entry_key} className="response-block"><strong>{PHASES[String(b.payload.phase)]} · {String(b.payload.start)} – {String(b.payload.recovery_end)}</strong><p>Край на натоварването: {String(b.payload.load_end)}. Пик: {fmt(b.summary?.peak_deviation)} лични отклонения; повишени дни: {b.summary?.elevated_days??0}; наблюдавани: {b.summary?.observed_days??0}/{b.summary?.tracked_days??0}.</p><p>{b.summary?.returned_on?`Наблюдавано връщане: ${b.summary.returned_on} (две последователни оценки).`:b.summary?.status==="IN_PROGRESS"?"Блокът продължава.":b.summary?.status==="REVIEW"?"Реакцията остава повишена в края на разтоварването.":"Недостатъчно данни за връщане към обичайното състояние."}</p></article>)}
      {canEditPlan&&<details><summary>Задай следващ блок за наблюдение</summary><SaveForm kind="block" build={f=>({start:f.get("start"),load_end:f.get("load_end"),recovery_end:f.get("recovery_end"),phase:f.get("phase"),components:f.getAll("components"),note:String(f.get("note")||""),expected_revision:0})}><legend>Контекст на блока</legend><p>Изграждащ блок за обучение: до 38 дни общо, включително поне 2 дни след натоварването. Запиши съпоставимия тест до 14 дни след разтоварването, докато цялата история е налична.</p><fieldset><legend>Компоненти · празно означава обща реакция</legend>{["Z1","Z2","Z3","Z4","Z5","STR"].map(z=><label key={z} className="response-check"><input name="components" type="checkbox" value={z}/>{z}</label>)}</fieldset><div className="response-fields"><label>Начало<input type="date" name="start" min={history.today} required/></label><label>Край на натоварващата част<input type="date" name="load_end" min={history.today} required/></label><label>Край на разтоварването<input type="date" name="recovery_end" min={history.today} required/></label><label>Фаза<select name="phase" defaultValue="BUILD">{Object.entries(PHASES).filter(([k])=>k!=="UNSPECIFIED").map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label></div><label>Бележка<textarea name="note" maxLength={500}/></label></SaveForm></details>}
    </section>
    <section className="response-card"><h2>Доброволни контролни тестове</h2><p>Използват се по преценка на треньора. Не влизат в дневната оценка. Съпоставимите резултати могат да участват в управлението на прираста след завършен блок, когато то е включено в профила. Липсата им не доказва успешна адаптация.</p>
      {history.tests.map(t=><p key={t.entry_key}>{String(t.payload.day)} · {String(t.payload.protocol)} ({String(t.payload.protocol_version)}): <strong>{String(t.payload.value)} {String(t.payload.unit)}</strong> · {t.payload.comparable?"условията са отбелязани като съпоставими":"съпоставимостта не е потвърдена"}{t.payload.load_observation_status==="UNAVAILABLE"&&<span className="management-error"> · Липсва запазена пълна товарна история за свързания блок. Резултатът е записан, но няма основание за обучение от този блок.</span>}{t.payload.load_observation_status==="ARCHIVED"&&<span> · Използва се съхранената товарна история на блока.</span>}</p>)}
      {canEditPlan&&<><button type="button" className="action-button secondary" onClick={()=>setShowTests(!showTests)}>{showTests?"Скрий формата":"Добави резултат по избор"}</button>{showTests&&<SaveForm kind="test" build={f=>({day:f.get("day"),protocol:f.get("protocol"),protocol_version:f.get("protocol_version"),value:Number(f.get("value")),unit:f.get("unit"),direction:f.get("direction"),conditions:f.get("conditions"),comparable:f.get("comparable")==="on",components:f.getAll("components"),meaningful_change_percent:Number(f.get("meaningful_change_percent")||1),expected_revision:0})}><legend>Резултат и условия</legend><fieldset><legend>Компоненти · празно означава обща реакция</legend>{["Z1","Z2","Z3","Z4","Z5","STR"].map(z=><label key={z} className="response-check"><input name="components" type="checkbox" value={z}/>{z}</label>)}</fieldset><label>Минимална значима промяна, %<input name="meaningful_change_percent" type="number" min=".01" max="20" step=".01" defaultValue="1" required/></label><div className="response-fields"><label>Дата<input name="day" type="date" max={history.today} required/></label><label>Протокол<input name="protocol" minLength={3} maxLength={100} required/></label><label>Версия<input name="protocol_version" maxLength={32} required/></label><label>Стойност<input name="value" type="number" step="any" min="0.000001" required/></label><label>Мерна единица<input name="unit" maxLength={24} required/></label><label>Подобрение при<select name="direction"><option value="HIGHER">По-висока стойност</option><option value="LOWER">По-ниска стойност</option></select></label></div><label>Трасе, екипировка и условия<textarea name="conditions" minLength={3} maxLength={500} required/></label><label className="response-check"><input name="comparable" type="checkbox"/>Потвърждавам съпоставими условия</label></SaveForm>}</>}
    </section>
    <section className="response-card response-method"><h2>Как е настроена оценката</h2><p>Субективно състояние 50%, отклонение на RPE 30%, пулс и HRV 20%. Теглата и праговете са работни пилотни настройки. Липсващ дял не се прехвърля към останалите.</p><p>За личния диапазон използваме медиана и устойчиво отклонение от 14–28 предходни дни. Блоковете имат фиксирана база. Отзвучаването се потвърждава с две последователни оценки след натоварващата част.</p><p>Оценките описват наблюдавана реакция, без да доказват пълно физиологично възстановяване. Recovery, Tref, 7/40, HRmod и Vflat запазват изчисленията си. Когато е включено в тренировъчния профил, управлението на прираста оценява завършени блокове със съпоставими тестове. Самата дневна оценка не променя Recovery.</p><Link href="/trainability">Виж и динамиката на индекса на тренираност →</Link></section>
  </>;
}
