"use client";
import { SymptomNotice } from "./symptom-notice";
import { useState } from "react";
import { SessionTrainingObservations, SavedTrainingObservations } from "./session-training-observations";
import { SaveForm } from "./response-save-form";
import { StressOverview } from "./stress-overview";
import { dailyInput, sessionInput } from "../lib/response-input";
import { BodyObservations } from "./body-observations";
import Link from "next/link";
import { DAILY_MAIN_FIELDS, DAILY_SCALES, FEEL_LABELS, FIELD_LABELS, REASONS, PHASES, type ResponseHistory, type ResponseDay, type Session, type ExecutionMethod } from "../lib/response-monitoring";

const fmt = (v:number|null|undefined) => v==null?"—":v.toLocaleString("bg-BG",{maximumFractionDigits:1});
const labelDate = (s:string) => s.slice(8,10)+"."+s.slice(5,7);

function DailyForm({day,today,canReport}:{day:ResponseDay;today:string;canReport:boolean}) {
  const report=day.daily_report;
  const rating = (key:string) => <label key={key}>{FIELD_LABELS[key]}<select name={key} defaultValue={report?.[key as keyof typeof report]==null?"":String(report[key as keyof typeof report])}><option value="">Не е попълнено</option>{DAILY_SCALES[key].map((text,i)=><option key={i} value={i+1}>{text}</option>)}</select></label>;
  return <SaveForm key={`${day.day}:${day.daily_revision}`} kind="daily" disabled={!canReport} build={form=>dailyInput(form,day,today)}>
    <legend>Сутрешна оценка · {labelDate(day.day)}</legend>
    <p>Пет кратки отговора за състоянието преди тренировката. Празният отговор означава липса на информация.</p>
    <div className="response-fields response-daily-fields">{DAILY_MAIN_FIELDS.map(rating)}</div>
    <label className="response-check"><input type="checkbox" name="pain_or_illness" defaultChecked={report?.pain_or_illness}/>Имам болка или симптоми, които искам да обсъдя с треньора</label>
    <details><summary>Допълни · по желание</summary>{rating("competition_motivation")}
      <label>Продължителност на съня · часове<input type="number" name="sleep_hours" min="0" max="24" step="0.1" defaultValue={report?.sleep_hours??""} placeholder="Използваме часовника, когато има данни"/></label>
      <label>Бележка<textarea name="note" maxLength={500} defaultValue={report?.note||""}/></label>
    </details>
    {!canReport&&<p>Личната оценка се въвежда от спортиста.</p>}
  </SaveForm>;
}

function ExecutedMethodFields({ session, methods }: { session: Session; methods: ExecutionMethod[] }) {
  const [selected, setSelected] = useState(session.execution?.executed_method_id ?? "");
  const [confirmed, setConfirmed] = useState(session.execution?.method_confirmed ?? false);
  return <details><summary>Изпълнен метод · за самообучението</summary>
    <label>Какъв метод изпълнихте?<select name="executed_method_id" value={selected} onChange={event => { setSelected(event.target.value); setConfirmed(false); }}>
      <option value="">Не е уточнен</option>
      {selected && !methods.some(method => method.id === selected) && <option value={selected}>Предишен метод · изберете актуален</option>}
      {methods.map(method => <option key={method.id} value={method.id}>{method.zone === "STR" ? "Сила" : method.zone} · {method.title}</option>)}
    </select></label>
    <label className="response-check"><input name="method_confirmed" type="checkbox" disabled={!selected} checked={confirmed} onChange={event => setConfirmed(event.target.checked)}/>Потвърждавам, че изпълних избрания метод</label>
    <p>Отбележете действително изпълненото. Само потвърдените методи участват в сравняването на реакцията към различните форми на тренировка.</p>
  </details>;
}

function SessionForm({session,canReport,methods,selected=false}:{session:Session;canReport:boolean;methods?:ExecutionMethod[];selected?:boolean}) {
  const [rpe,setRpe] = useState(session.rpe==null?"":String(session.rpe));
  const needsTiming = rpe!=="" && !(Number(rpe)===session.rpe && (session.rpe_source??session.source)==="INTERVALS");
  const sourceLabel = (source:string|null|undefined) => source==="ONFLOWS"?"onFlows":source==="INTERVALS"?"Intervals":"няма отговор";
  return <details className="response-session" id={`session-${session.activity_ref}`} open={selected || undefined}><summary><span>{labelDate(session.day)} · {session.name}</span><span>Усилие {fmt(session.rpe)}/10 · {session.feel==null?"Усещането не е попълнено":FEEL_LABELS[session.feel-1]}</span></summary>
    <p><Link href={`/activities/${session.activity_ref}`}>Отвори активността</Link> · Усилие: {sourceLabel(session.rpe_source??session.source)} · Усещане: {sourceLabel(session.feel_source)}</p>
    <SaveForm key={`${session.activity_ref}:${session.revision}`} kind="session" disabled={!canReport} build={form=>sessionInput(form,session,!!methods)}>
      <legend>Какво усилие изискваше цялата тренировка?</legend>
      <div className="response-fields"><label>Усилие RPE · 0–10<select name="rpe" value={rpe} onChange={event=>setRpe(event.target.value)}><option value="">Не е попълнено</option>{Array.from({length:11},(_,i)=><option value={i} key={i}>{i}{i===0?" · без усилие":i===3?" · умерено":i===5?" · тежко":i===10?" · максимално":""}</option>)}</select></label>
      <label>Как се чувствахте по време на тренировката?<select name="feel" defaultValue={session.feel??""}><option value="">Не е попълнено</option>{FEEL_LABELS.map((label,i)=><option key={label} value={i+1}>{label}</option>)}</select></label></div>
      {(session.rpe_source==="INTERVALS" || session.feel_source==="INTERVALS")&&<p>Получените оценки от Intervals са попълнени. Допълнете само липсващото.</p>}
      <details open={needsTiming && session.timing==="UNKNOWN" || undefined}><summary>Време и момент на оценката</summary><div className="response-fields">
      <label>Цяла сесия, минути<input name="duration_minutes" type="number" min="0.1" max="1440" step="0.1" required defaultValue={session.duration_minutes??(session.suggested_duration_minutes?Math.round(session.suggested_duration_minutes*10)/10:"")}/></label>
      <label>Кога направихте оценката?<select name="timing" required={needsTiming} defaultValue={session.timing==="UNKNOWN"?"":session.timing}><option value="">Не е уточнено</option><option value="IMMEDIATE">Непосредствено след края</option><option value="DELAYED">Около 10–60 минути след края</option><option value="NEXT_DAY">На следващия ден</option></select></label></div>
      <p>Времето включва почивките. {session.duration_source==="ACTIVITY"?"Взето е от записа на активността; можете да го поправите. ":""}Натоварване по усещане: {fmt(session.srpe_load)} (RPE × минути). Не се прибавя към Q.{session.timing==="UNKNOWN"?" Моментът на внесената оценка е неизвестен; тя не участва в сравняването на RPE при сходна работа.":""}</p></details>
      <details><summary>Съпостави с плана · по желание</summary><p>Сравнете една и съща част от тренировката. Уточнете причината за отклонението; време, условия и треньорско решение не се оценяват като умора.</p><div className="response-fields"><label>Планирана цяла сесия · минути<input name="planned_duration_minutes" type="number" min="0.1" max="1440" step="0.1" defaultValue={session.execution?.planned_duration_minutes??""}/></label><label>Планирана скорост · km/h<input name="planned_speed_kmh" type="number" min="0.01" max="150" step="0.01" defaultValue={session.execution?.planned_speed_kmh??""}/></label><label>Изпълнена скорост · km/h<input name="executed_speed_kmh" type="number" min="0.01" max="150" step="0.01" defaultValue={session.execution?.executed_speed_kmh??""}/></label><label>Изпълнение / причина<select name="execution_reason" defaultValue={session.execution?.execution_reason??"UNKNOWN"}>{Object.entries(REASONS).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label></div><label className="response-check"><input name="execution_comparable" type="checkbox" defaultChecked={session.execution?.execution_comparable??false}/>Съпоставими условия, метод и част от тренировката</label></details>
      <details><summary>Бележка · по желание</summary><label>Бележка<textarea name="note" maxLength={500} defaultValue={session.note}/></label></details>
      <SessionTrainingObservations key={`observations:${session.activity_ref}:${session.revision}`} session={session}/>
      {methods && <ExecutedMethodFields key={`${session.activity_ref}:${session.revision}`} session={session} methods={methods.filter(method => method.sports.includes(session.sport))}/>}
    </SaveForm>
    <SavedTrainingObservations session={session}/>
    <p>{session.expected_rpe==null?`Нужни са поне 3 предишни съпоставими сесии. Налични: ${session.comparable_count}.`:`Обичайно RPE при сходна работа: ${fmt(session.expected_rpe)}; днес: ${fmt(session.rpe)}.`}</p>
  </details>;
}

export function ResponseMonitoring({history,canReport,canEditPlan,selectedActivity}:{history:ResponseHistory;canReport:boolean;canEditPlan:boolean;selectedActivity?:string}) {
  const [selected,setSelected]=useState(history.days.length-1);
  const [showTests,setShowTests]=useState(false);
  const day=history.days[Math.min(selected,history.days.length-1)];
  return <>
    {history.symptom_context?.hold_for_reported_illness_or_pain && <SymptomNotice day={history.symptom_context.latest_report_day}/>}
    {day&&<section className="response-card" id="daily-report"><details open={!day.daily_report}><summary>Сутрешна оценка · {labelDate(day.day)}</summary><DailyForm day={day} today={history.today} canReport={canReport}/></details></section>}
    <StressOverview history={history} selected={selected} onSelect={setSelected}/>
    {day&&<details className="response-card stress-observations"><summary>Тегло и изследвания · {labelDate(day.day)}</summary><BodyObservations day={day} history={history} canReport={canReport} canEditPlan={canEditPlan}/></details>}
    <section className="response-card"><details open={!!selectedActivity || undefined}><summary>След тренировка · усилие и усещане</summary><p>Оценките от часовника се показват, когато Intervals ги е предал. Вашите поправки се запазват при следващ импорт.</p>{history.sessions.length?history.sessions.slice().reverse().map(s=><SessionForm key={`${s.activity_ref}:${s.revision}:${s.rpe}:${s.feel}`} session={s} canReport={canReport} methods={history.execution_methods} selected={s.activity_ref === selectedActivity}/>):<p>Няма активности в периода.</p>}</details></section>
    <section className="response-card"><details><summary>Реакция и отзвучаване по блокове</summary><p>Задайте предварително натоварващата част и края на разтоварването. Опорната база се фиксира при създаването. Започнал блок не се удължава със задна дата.</p><p>Обобщението тук следи субективното състояние. Проверявайте успоредно пулса, HRV и възприетото усилие в графиката. Прекъсване на оценките или ново покачване отменя потвърденото отзвучаване.</p>
      {history.blocks.map(b=><article key={b.entry_key} className="response-block"><strong>{PHASES[String(b.payload.phase)]} · {String(b.payload.start)} – {String(b.payload.recovery_end)}</strong><p>Край на натоварването: {String(b.payload.load_end)}. Пик: {fmt(b.summary?.peak_deviation)} лични отклонения; повишени дни: {b.summary?.elevated_days??0}; наблюдавани: {b.summary?.observed_days??0}/{b.summary?.tracked_days??0}.</p><p>{b.summary?.returned_on?`Наблюдавано връщане: ${b.summary.returned_on} (две последователни оценки).`:b.summary?.status==="IN_PROGRESS"?"Блокът продължава.":b.summary?.status==="REVIEW"?"Реакцията остава повишена в края на разтоварването.":"Недостатъчно данни за връщане към обичайното състояние."}</p></article>)}
      {canEditPlan&&<details><summary>Задай следващ блок за наблюдение</summary><SaveForm kind="block" build={f=>({start:f.get("start"),load_end:f.get("load_end"),recovery_end:f.get("recovery_end"),phase:f.get("phase"),components:f.getAll("components"),note:String(f.get("note")||""),expected_revision:0})}><legend>Контекст на блока</legend><p>Изграждащ блок за обучение: до 38 дни общо, включително поне 2 дни след натоварването. Запиши съпоставимия тест до 14 дни след разтоварването, докато цялата история е налична.</p><fieldset><legend>Компоненти · празно означава обща реакция</legend>{["Z1","Z2","Z3","Z4","Z5","STR"].map(z=><label key={z} className="response-check"><input name="components" type="checkbox" value={z}/>{z}</label>)}</fieldset><div className="response-fields"><label>Начало<input type="date" name="start" min={history.today} required/></label><label>Край на натоварващата част<input type="date" name="load_end" min={history.today} required/></label><label>Край на разтоварването<input type="date" name="recovery_end" min={history.today} required/></label><label>Фаза<select name="phase" defaultValue="BUILD">{Object.entries(PHASES).filter(([k])=>k!=="UNSPECIFIED").map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label></div><label>Бележка<textarea name="note" maxLength={500}/></label></SaveForm></details>}
    </details></section>
    <section className="response-card"><details><summary>Доброволни контролни тестове</summary><p>Използват се по преценка на треньора. При поне три предишни съпоставими резултата участват в оценката за деня на теста. Съпоставимите резултати могат да участват в управлението на прираста след завършен блок, когато то е включено в профила. Липсата им не доказва успешна адаптация.</p>
      {history.tests.map(t=><p key={t.entry_key}>{String(t.payload.day)} · {String(t.payload.protocol)} ({String(t.payload.protocol_version)}): <strong>{String(t.payload.value)} {String(t.payload.unit)}</strong> · {t.payload.comparable?"условията са отбелязани като съпоставими":"съпоставимостта не е потвърдена"}{t.payload.load_observation_status==="UNAVAILABLE"&&<span className="management-error"> · Липсва запазена пълна товарна история за свързания блок. Резултатът е записан, но няма основание за обучение от този блок.</span>}{t.payload.load_observation_status==="ARCHIVED"&&<span> · Използва се съхранената товарна история на блока.</span>}</p>)}
      {canEditPlan&&<><button type="button" className="action-button secondary" onClick={()=>setShowTests(!showTests)}>{showTests?"Скрий формата":"Добави резултат по избор"}</button>{showTests&&<SaveForm kind="test" build={f=>({day:f.get("day"),protocol:f.get("protocol"),protocol_version:f.get("protocol_version"),value:Number(f.get("value")),unit:f.get("unit"),direction:f.get("direction"),conditions:f.get("conditions"),comparable:f.get("comparable")==="on",components:f.getAll("components"),meaningful_change_percent:Number(f.get("meaningful_change_percent")||1),expected_revision:0})}><legend>Резултат и условия</legend><fieldset><legend>Компоненти · празно означава обща реакция</legend>{["Z1","Z2","Z3","Z4","Z5","STR"].map(z=><label key={z} className="response-check"><input name="components" type="checkbox" value={z}/>{z}</label>)}</fieldset><label>Минимална значима промяна, %<input name="meaningful_change_percent" type="number" min=".01" max="20" step=".01" defaultValue="1" required/></label><div className="response-fields"><label>Дата<input name="day" type="date" max={history.today} required/></label><label>Протокол<input name="protocol" minLength={3} maxLength={100} required/></label><label>Версия<input name="protocol_version" maxLength={32} required/></label><label>Стойност<input name="value" type="number" step="any" min="0.000001" required/></label><label>Мерна единица<input name="unit" maxLength={24} required/></label><label>Подобрение при<select name="direction"><option value="HIGHER">По-висока стойност</option><option value="LOWER">По-ниска стойност</option></select></label></div><label>Трасе, екипировка и условия<textarea name="conditions" minLength={3} maxLength={500} required/></label><label className="response-check"><input name="comparable" type="checkbox"/>Потвърждавам съпоставими условия</label></SaveForm>}</>}
    </details></section>
    <section className="response-card response-method"><details><summary>Как е настроена оценката</summary><p>Субективно състояние 35%, работоспособност 25%, пулс и HRV 15%, тегло 15%, биохимия 10%. Теглата са начални експертни настройки за тестване. Наличните показатели се претеглят отново; покритието показва колко от модела действително наблюдаваме.</p><p>Личната база е от предходните 28 дни, с поне 14 наблюдения. Сутрешният индекс изисква 4 измервания за 7 календарни дни. Лабораторните сравнения и тестовете изискват 3 предишни съпоставими дни. Старите измервания не се пренасят като днешни.</p><p>Ниската оценка не задейства увеличение на натоварването. Самообучението използва наблюденията заедно с изпълненото натоварване и последващите резултати. Режимът му се задава в профила за планиране; ограниченията на Recovery остават в сила.</p></details></section>
  </>;
}
