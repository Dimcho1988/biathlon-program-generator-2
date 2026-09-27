"use client";
import {useRef} from "react";
import {SaveForm} from "./response-save-form";
import {ANALYTES, labPayload, weightPayload, type Analyte, type LabReport, type WeightContext} from "../lib/body-observations";
import type {ResponseDay, ResponseHistory} from "../lib/response-monitoring";

const fmt=(v:number|null|undefined,digits=2)=>v==null?"—":v.toLocaleString("bg-BG",{maximumFractionDigits:digits});
const dayCount=(days:number)=>days===1?"1 ден":`${days} дни`;
const STATUSES:Record<string,string>={LOW:"Под въведения диапазон",HIGH:"Над въведения диапазон",WITHIN_PROVIDED_LIMITS:"Във въведените граници",NO_REFERENCE:"Няма въведени граници",CENSORED:"Гранична стойност · не е точно измерване"};
const SAMPLES={SERUM:"Серум",PLASMA:"Плазма",WHOLE_BLOOD:"Цяла кръв",SALIVA:"Слюнка"};

function WeightForm({day,weight,canReport}:{day:ResponseDay;weight:WeightContext;canReport:boolean}) {
  const report=weight.report;
  const imported=day.device_metrics.weight?.value;
  return <details><summary>Въведи или коригирай теглото за {day.day}</summary>
    <SaveForm kind="weight" disabled={!canReport} build={f=>weightPayload(f,day.day,weight.revision)}>
      <legend>Измервания в kg · {day.day}</legend>
      <p>Сутрин: след тоалетна, преди храна и течности, на същия кантар и при еднакво облекло. Потвърдете условията, за да участва измерването в индекса.</p>
      {!report&&imported!=null&&<p>Предложено от Intervals: {fmt(imported)} kg. Часът и условията не са потвърдени.</p>}
      <label>Сутрешно тегло<input name="morning_kg" type="number" min="0.01" max="500" step="0.01" defaultValue={report?report.morning_kg??"":imported??""}/></label>
      <label className="response-check"><input type="checkbox" name="morning_standardized" defaultChecked={report?.morning_standardized??false}/>Измерено при описаните сутрешни условия</label>
      {[1,2,3,4].map(session=>{const p=report?.sessions.find(p=>p.session===session);return <details key={session}><summary>Тренировка {session}{p?" · има измервания":" · по желание"}</summary>
        <div className="response-fields"><label>Преди · kg<input name={`before_${session}`} type="number" min="0.01" max="500" step="0.01" defaultValue={p?.before_kg??""}/></label><label>След · kg<input name={`after_${session}`} type="number" min="0.01" max="500" step="0.01" defaultValue={p?.after_kg??""}/></label><label>Приети течности · L<input name={`fluid_${session}`} type="number" min="0" max="30" step="0.01" defaultValue={p?.fluid_l??""}/></label><label>Отделена урина · L<input name={`urine_${session}`} type="number" min="0" max="30" step="0.01" defaultValue={p?.urine_l??""}/></label></div>
        <label className="response-check"><input type="checkbox" name={`comparable_${session}`} defaultChecked={p?.comparable??false}/>Една и съща сесия, кантар и съпоставимо сухо облекло</label>
      </details>;})}
      <p>Може да запишете само „преди“ и да допълните „след“ по-късно. Течностите и урината са контекст; отношението след/преди не е коригирана загуба на пот.</p>
      <details><summary>Телесен състав · по желание</summary><div className="response-fields"><label>Вода · %<input name="body_water_percent" type="number" min="0.01" max="100" step="0.1" defaultValue={report?.body_water_percent??""}/></label><label>Мазнини · %<input name="body_fat_percent" type="number" min="0" max="100" step="0.1" defaultValue={report?.body_fat_percent??""}/></label><label>Уред / метод<input name="composition_method" maxLength={80} defaultValue={report?.composition_method??""}/></label></div><p>Контекст за теглото. Не се броят като отделни сигнали, защото биоимпедансните оценки зависят от хидратацията.</p></details>
      <label>Хранене, условия и друга бележка<textarea name="weight_note" maxLength={500} defaultValue={report?.note??""}/></label>
      {!canReport&&<p>Теглото се въвежда от спортиста.</p>}
    </SaveForm>
  </details>;
}

function LabForm({day,report,canEdit}:{day:string;report?:LabReport;canEdit:boolean}) {
  const sampleId=useRef<string|null>(report?.entry_key??null);
  const p=report?.payload;
  return <SaveForm kind="lab" disabled={!canEdit} build={f=>{sampleId.current??=crypto.randomUUID().replaceAll("-","");return labPayload(f,day,sampleId.current,report?.revision??0);}}>
    <legend>Изследване · {day}</legend>
    <div className="response-fields"><label>Час на пробовземане · местно време<input name="collection_time" type="time" defaultValue={p?.collection_time??""}/></label><label>Лаборатория<input name="laboratory" required maxLength={100} defaultValue={p?.laboratory??""}/></label><label>Протокол / условия за сравнение<input name="protocol" required maxLength={100} placeholder="Напр. сутрин, 48 h след натоварване" defaultValue={p?.protocol??""}/></label><label>Часове след последната тренировка<input name="hours_since_training" type="number" min="0" max="8760" step="0.1" defaultValue={p?.hours_since_training??""}/></label><label>На гладно<select name="fasting" defaultValue={p?.fasting??"UNKNOWN"}><option value="UNKNOWN">Не е известно</option><option value="YES">Да</option><option value="NO">Не</option></select></label></div>
    <label className="response-check"><input name="lab_comparable" type="checkbox" defaultChecked={p?.comparable??false}/>Потвърждавам съпоставими условия, метод и време след натоварване за този протокол</label>
    <p>Попълнете измерените показатели. Празното поле означава липсващо изследване. Въведете границите от лабораторния документ в същите единици като резултата. Общият тестостерон не се заменя със свободен.</p>
    {(Object.keys(ANALYTES) as Analyte[]).map(k=>{const r=p?.results.find(r=>r.analyte===k);return <details key={k} open={!!r}><summary>{ANALYTES[k].label}{r?` · ${fmt(r.value)} ${r.unit}`:""}</summary><div className="response-fields">
      <label>{ANALYTES[k].label} · резултат<input name={`${k}_value`} type="number" min="0" max={k==="TSAT"?100:10000000} step="any" defaultValue={r?.value??""}/></label>
      <label>Единица<select name={`${k}_unit`} defaultValue={r?.unit??ANALYTES[k].units[0]}>{ANALYTES[k].units.map(u=><option key={u}>{u}</option>)}</select></label>
      <label>Знак<select name={`${k}_qualifier`} defaultValue={r?.qualifier??"EQ"}><option value="EQ">Точна стойност</option><option value="LT">По-малко от (&lt;)</option><option value="GT">По-голямо от (&gt;)</option></select></label>
      <label>Материал<select name={`${k}_sample`} defaultValue={r?.sample??(k==="HEMOGLOBIN"?"WHOLE_BLOOD":"SERUM")}>{Object.entries(SAMPLES).map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>
      <label>Долна граница<input name={`${k}_low`} type="number" min="0" step="any" defaultValue={r?.reference_low??""}/></label><label>Горна граница<input name={`${k}_high`} type="number" min="0" step="any" defaultValue={r?.reference_high??""}/></label>
    </div></details>;})}
    <label>Контекст · симптоми, хранене, хидратация, лекарства, надморска височина, цикъл<textarea name="lab_note" maxLength={500} defaultValue={p?.note??""}/></label>
    <p>Сравнение с лична медиана се показва след поне 3 предишни дни при същата лаборатория, протокол, материал, часови интервал и статус на гладно. Това не е праг за диагностика.</p>
  </SaveForm>;
}

function LabCard({report,selectedDay,today,canEdit}:{report:LabReport;selectedDay:string;today:string;canEdit:boolean}) {
  const p=report.payload;
  const age=Math.round((Date.parse(`${selectedDay}T00:00:00Z`)-Date.parse(`${p.day}T00:00:00Z`))/86400000);
  const editable=Date.parse(`${today}T00:00:00Z`)-Date.parse(`${p.day}T00:00:00Z`)<=90*86400000;
  return <article className="response-block"><h3>{p.day} {p.collection_time??""} · {p.laboratory}</h3><p>{age===0?"Изследване от избрания ден.":`Изследване отпреди ${dayCount(age)} спрямо избрания ден; не описва автоматично текущото състояние.`} Протокол: {p.protocol}.</p>
    {report.outside_reference&&<p className="body-observation-notice" role="note">Има стойност извън въведените лабораторни граници. Обсъдете я със спортния лекар в контекста на пробовземането и натоварването. Общата оценка не отменя този сигнал.</p>}
    <div className="response-table"><table><thead><tr><th>Показател</th><th>Резултат</th><th>Граници</th><th>Контекст</th><th>Лична история</th></tr></thead><tbody>{report.results.map(r=><tr key={r.analyte}><th>{ANALYTES[r.analyte].label}</th><td>{r.qualifier==="LT"?"<":r.qualifier==="GT"?">":""}{fmt(r.value)} {r.unit}<br/>{SAMPLES[r.sample]}</td><td>{fmt(r.reference_low)} – {fmt(r.reference_high)} {r.unit}</td><td>{STATUSES[r.reference_status??"NO_REFERENCE"]}</td><td>{r.baseline_median==null?`${r.baseline_count??0}/3 предишни дни`:`Медиана ${fmt(r.baseline_median)} ${r.normalized_unit}; промяна ${fmt(r.change_percent)}% (${r.baseline_count} дни)`}</td></tr>)}</tbody></table></div>
    <p>Тестостерон / кортизол: <strong>{fmt(report.testosterone_cortisol_ratio,4)}</strong> · моларно отношение, само от тази проба и същия материал. Не е самостоятелна оценка на претрениране.</p>
    {p.note&&<p>{p.note}</p>}
    {canEdit&&editable&&<details><summary>Коригирай изследването</summary><LabForm key={`${report.entry_key}:${report.revision}`} day={p.day} report={report} canEdit={canEdit}/></details>}
  </article>;
}

export function BodyObservations({day,history,canReport,canEditPlan}:{day:ResponseDay;history:ResponseHistory;canReport:boolean;canEditPlan:boolean}) {
  const context=day.body_observations;
  if(!context)return null;
  const w=context.weight;
  const available=(history.lab_reports??[]).filter(r=>r.payload.day<=day.day).slice().reverse();
  const current=available.filter(r=>r.payload.day===day.day);
  const previous=available.filter(r=>r.payload.day<day.day);
  return <section className="body-observations-content" aria-label="Тегло и биохимичен контекст">
    <p>Теглото участва с базово тегло 15%, биохимията — с 10%, когато има съпоставими данни. Това са работни настройки за тестване.</p>
    {context.context_status==="REVIEW_LAB_REFERENCE"&&<p className="body-observation-notice">За този ден има лабораторен резултат извън въведените граници — вижте отделния сигнал по-долу.</p>}
    <div className="response-detail-grid"><div><h3>Индекс по сутрешно тегло</h3><strong className="body-index">{fmt(w.morning_ratio,4)}</strong><p>Сутрешно тегло за избрания ден / средно за последните 7 календарни дни, включително избрания.</p><p>Средно: {fmt(w.morning_mean_kg)} kg · {w.morning_count}/7 съпоставими измервания. Нужни са поне {w.minimum_morning_days} и измерване за избрания ден.</p><p>Промяна спрямо средното: {fmt(w.morning_change_percent)}%. Стойност под 1 означава тегло под средното. Влияят гликогенът, водата, храненето и условията на измерване.</p></div>
      <div><h3>Индекс след / преди тренировка</h3><strong className="body-index">{fmt(w.session_ratio,4)}</strong><p>Средно „след“ / средно „преди“, само от същите завършени двойки. Съпоставими сесии: {w.paired_sessions}.</p>{w.sessions.map(p=><p key={p.session}>Сесия {p.session}: {fmt(p.before_kg)} → {fmt(p.after_kg)} kg · спад {fmt(p.mass_loss_percent)}%{p.ratio==null?" · липсва двойка или потвърдени условия":""}</p>)}<p>Това е промяна на телесната маса около сесията, а не пряко измерване на умората.</p></div>
      <div><h3>Пропуски и увереност</h3><p>Липсващите дни не се запълват. След прекъсване използваме само измерванията в текущия 7-дневен прозорец.</p><p>{w.gap_days==null?"Няма предишно сутрешно измерване.":`Предишно записано сутрешно измерване: преди ${dayCount(w.gap_days)}.`}</p><p>Броят измервания описва покритието, а не научно валидирана вероятност.</p></div></div>
    <WeightForm key={`${day.day}:${w.revision}`} day={day} weight={w} canReport={canReport}/>
    <h3>Лабораторни изследвания</h3>
    {current.length?current.map(r=><LabCard key={r.entry_key} report={r} selectedDay={day.day} today={history.today} canEdit={canEditPlan}/>):<p>Няма лабораторно изследване от избрания ден.</p>}
    {previous.length>0&&<details><summary>По-ранни изследвания · {previous.length}</summary>{previous.map(r=><LabCard key={r.entry_key} report={r} selectedDay={day.day} today={history.today} canEdit={canEditPlan}/>)}</details>}
    {canEditPlan&&<details><summary>Добави изследване за {day.day}</summary><LabForm key={`new:${day.day}:${current.length}`} day={day.day} canEdit={canEditPlan}/></details>}
  </section>;
}
