"use client";
import Link from "next/link";
import {useState} from "react";
import {useRouter} from "next/navigation";
import {saveModel,type SpeedModel,type SpeedTest,type Prediction} from "../lib/models";
import {HrSpeedZones} from "./hr-speed-zones";
import {SpeedTestEditor} from "./speed-test-editor";
import {clockTime,manualClockTime,manualTestPayload} from "../lib/speed-tests";
import {ManualSpeedTestEditor} from "./manual-speed-test";
import {AthleteFunctionalProfile} from "./athlete-functional-profile";
const n=(v:number)=>new Intl.NumberFormat("bg-BG",{maximumFractionDigits:2}).format(v);
const time=clockTime;
const evidenceLabels={MEASURED:"Реален максимален тест",INTERPOLATED:"Оценка между реалните тестове",EXTRAPOLATED:"Прогноза извън реалните тестове",ESTIMATED:"Предварителна оценка"};
function evidenceLabel(point:Prediction,model:SpeedModel){
  if(point.evidence)return evidenceLabels[point.evidence];
  if(model.status==="PRELIMINARY")return evidenceLabels.ESTIMATED;
  const window=model.curve_metadata?.measured_window_s;
  return window?(point.duration_s<window[0]||point.duration_s>window[1]?evidenceLabels.EXTRAPOLATED:evidenceLabels.INTERPOLATED):null;
}

export function SpeedModelPanel({model,canEdit,activityRef,predictionInput="minutes",predictionValue="3"}:{model:SpeedModel;canEdit:boolean;activityRef?:string;predictionInput?:string;predictionValue?:string}){
  const router=useRouter();
  const exploratory=model.tests.some(t=>model.active_test_keys.includes(t.entry_key)&&t.payload.test_mode==="EXPLORATORY");
  const [testSource,setTestSource]=useState<"ACTIVITY"|"MANUAL">("ACTIVITY");
  const [editing,setEditing]=useState<SpeedTest|undefined>();
  const [manualKey,setManualKey]=useState(0);
  const [busy,setBusy]=useState(false),[message,setMessage]=useState("");
  const [input,setInput]=useState(predictionInput),[value,setValue]=useState(predictionValue);
  const [cursor,setCursor]=useState(60);
  const [pending,setPending]=useState<{key:string;revision:number}|null>(null);
  const arrived=pending&&model.tests.some(t=>t.entry_key===pending.key&&t.revision>=pending.revision);
  const waiting=Boolean(pending&&!arrived);
  const points=model.curve_metadata?.absolute_speed_available===false||model.status==="UNAVAILABLE"||model.status==="CONFLICTING_TESTS"?[]:model.points;
  const sample=points[Math.min(cursor,points.length-1)];
  const first=points[0],last=points.at(-1);
  const hrUnavailable=input==="hr"&&!model.hr_model;
  const canPredict=points.length>1&&(model.status==="CALIBRATED"||model.status==="PRELIMINARY")&&!hrUnavailable;
  const limits=input==="hr"?(model.hr_model?.hr_range_bpm??[undefined,undefined]):!first||!last?[undefined,undefined]:input==="minutes"?[first.duration_s/60,last.duration_s/60]:input==="km"?[first.distance_m/1000,last.distance_m/1000]:[last.speed_kmh,first.speed_kmh];
  const measuredWindow=model.curve_metadata?.measured_window_s;
  const curveLabel=exploratory?"Пробна крива":model.status==="CALIBRATED"?"Индивидуална крива":model.status==="PRELIMINARY"?"Предварителна индивидуална крива":model.status==="CONFLICTING_TESTS"?"Нужен е преглед на тестовете":model.status==="UNAVAILABLE"?"Все още няма индивидуална скоростна оценка":"Референтна крива";
  const lo=Math.log(model.points[0]?.duration_s||10.8),hi=Math.log(model.points.at(-1)?.duration_s||43516);
  const vmax=Math.ceil((model.points[0]?.speed_kmh||40)/5)*5;
  const x=(t:number)=>48+852*(Math.log(t)-lo)/(hi-lo),y=(v:number)=>270-230*v/vmax;
  async function toggle(t:SpeedTest){
    setBusy(true);setMessage("");setPending(null);
    const p=t.payload;
    try{const result=p.source==="MANUAL"?await saveModel("speed-test-manual",manualTestPayload(t,!p.enabled)):await saveModel("speed-test",{activity_ref:p.activity_ref,start_s:p.start_s,duration_s:p.duration_s,maximal:p.maximal,test_mode:p.test_mode??"STRICT",exploratory_confirmed:p.test_mode==="EXPLORATORY",comparable:true,enabled:!p.enabled,use_for_cs:p.use_for_cs,conditions:p.conditions,expected_revision:t.revision});setPending({key:t.entry_key,revision:result.revision});router.refresh();}
    catch(e){setMessage(e instanceof Error?e.message:"Неуспешен запис.");}finally{setBusy(false);}
  }
  return <>
    <section className="history-section"><form method="get" className="model-controls"><label>Спорт<select name="sport" defaultValue={model.sport}>{[...new Set([model.sport,...model.sports])].map(s=><option key={s}>{s}</option>)}</select></label><button className="action-button secondary">Покажи</button></form>
      <p className="speed-model-status"><strong>{curveLabel}</strong>{model.active_test_count>0?` · ${model.active_test_count} активни ${exploratory?"контролни точки":"максимални теста"}`:""}</p>
      {(model.status==="REFERENCE_ONLY"||model.status==="UNAVAILABLE")&&<div className="speed-onboarding"><strong>За {model.sport} още няма достатъчна основа за индивидуална скорост.</strong><p>{model.status==="REFERENCE_ONLY"?"Кривата показва експертен пример. Индивидуалното изчисление се отключва след максимален тест или достатъчно валидни данни за връзката пулс–скорост.":"Историята и експертните времеви граници могат да насочват тренировъчната доза. За скоростта е нужен максимален тест или валидна връзка между пулса и скоростта."}</p><a className="action-button" href="#speed-tests">Добави тест за индивидуална крива</a></div>}
      {model.status==="PRELIMINARY"&&<div className="speed-onboarding"><strong>Ориентир, който предстои да проверим с тест.</strong><p>Кривата съчетава историята, експертните времеви граници и наличната връзка пулс–скорост. Тези оценки не са измерени максимални възможности.</p><a href="#speed-tests">Добави максимален тест за по-надеждна оценка →</a></div>}
      {exploratory&&<div className="speed-onboarding"><strong>Пробна калибрация</strong><p>В кривата участват записи от комплексни тренировки с по-ниско покритие. Прогнозата е за тестване на модела. Критичната скорост използва само стандартните максимални тестове.</p></div>}
      {(model.status==="CONFLICTING_TESTS"||model.warnings.includes("CONFLICTING_TESTS"))&&<p role="alert">Избраните тестове не позволяват съгласувана крива. Индивидуалните прогнози са спрени. <a href="#speed-tests">Провери времето, скоростта и условията на тестовете</a>; изключи несъпоставим запис или го коригирай.</p>}
      {model.warnings.includes("INCOMPARABLE_MODEL_VERSIONS")&&<p role="alert">Тестовете използват различни версии на Vflat. Изключете или преизчислете старите тестове, преди да ги сравнявате.</p>}
      {model.warnings.includes("INCOMPARABLE_INDEX_CONFIGURATION")&&<p role="status">Има несъпоставими резултати след промяна на настройките. Обновете активностите и при нужда запишете тестовете отново. При липса на подходящ ТИ връзката пулс–скорост използва експертните ориентири.</p>}
      {Boolean(model.index_admission?.refresh_required)&&<p role="status">Има активности за преизчисляване с актуалния модел. Отвори <Link href="/">началния екран</Link> → „Обнови данните“ и обнови анализите, за да участват техните индекси във връзката пулс–скорост.</p>}
      {sample&&<figure className="history-chart"><svg viewBox="0 0 920 320" role="img" aria-label={exploratory?"Пробна скорост според продължителността":"Средна максимална скорост според продължителността"}>
        {[0,.25,.5,.75,1].map(f=><g key={f}><line x1="48" x2="900" y1={y(vmax*f)} y2={y(vmax*f)} stroke="currentColor" opacity=".12"/><text x="40" y={y(vmax*f)+4} textAnchor="end" fill="currentColor" fontSize="12">{n(vmax*f)}</text></g>)}
        {points.slice(1).map((p,i)=>{const previous=points[i];const estimated=p.evidence==="EXTRAPOLATED"||p.evidence==="ESTIMATED"||model.status==="PRELIMINARY"||(measuredWindow&&(previous.duration_s<measuredWindow[0]||p.duration_s>measuredWindow[1]));return <line key={p.duration_s} x1={x(previous.duration_s)} y1={y(previous.speed_kmh)} x2={x(p.duration_s)} y2={y(p.speed_kmh)} stroke="var(--accent,#41b88c)" strokeWidth="3" strokeDasharray={estimated?"6 4":undefined}/>;})}
        {model.tests.filter(t=>model.active_test_keys.includes(t.entry_key)).map(t=><circle key={t.entry_key} cx={x(t.payload.duration_s)} cy={y(t.payload.speed_kmh)} r="5" fill="#ef9c45"><title>{`${t.payload.source==="MANUAL"?"Ръчен · ":t.payload.test_mode === "EXPLORATORY" ? "Пробен · " : ""}${t.payload.day} · ${manualClockTime(t.payload.duration_s)} · ${n(t.payload.speed_kmh)} км/ч`}</title></circle>)}
        {[60,180,720,3600,21600].filter(t=>Math.log(t)>=lo&&Math.log(t)<=hi).map(t=><text key={t} x={x(t)} y="297" textAnchor="middle" fill="currentColor" fontSize="12">{t/60} мин</text>)}
        <text x="48" y="20" fill="currentColor" fontSize="12">км/ч · Vflat</text>
      </svg><div className="speed-curve-readout"><strong>{curveLabel}: {time(sample.duration_s)} · {n(sample.speed_kmh)} км/ч · {n(sample.distance_m/1000)} км</strong>{evidenceLabel(sample,model)&&<span>{evidenceLabel(sample,model)}{sample.capped?" · достигнато ограничение на допълнителното отклонение":""}</span>}<label>Разгледай кривата<input type="range" min="0" max={points.length-1} step="1" value={Math.min(cursor,points.length-1)} onChange={e=>setCursor(Number(e.target.value))}/></label></div><figcaption>Точките са записани тестови резултати. Прекъснатата линия показва предварителна оценка или прогноза извън тестовете. Времето е по логаритмична скала.</figcaption></figure>}
      {measuredWindow&&<p>Проверен с тестове диапазон: {time(measuredWindow[0])}{measuredWindow[1]!==measuredWindow[0]?` – ${time(measuredWindow[1])}`:""}. Стойностите между тестовете също са моделни оценки.</p>}
      {model.curve_metadata?.mode==="SINGLE_ANCHOR_SCALE"&&<p>При един максимален тест запазваме формата на нормативната крива и мащабираме скоростта, за да премине точно през резултата.</p>}
      {model.curve_metadata?.cap_percent!=null&&<details className="speed-method-note"><summary>Как се ограничава прогнозата извън тестовете?</summary><p>Кривата преминава точно през всички реални тестове. Извън тях продължаваме формата на крайния измерен участък. Допълнителното отклонение от индивидуално мащабираната нормативна основа е до {n(model.curve_metadata.cap_percent)}%, с плавен преход и без обръщане на посоката на отклонението. Двата края се разглеждат независимо. Това е ограничение на модела, а не граница на възможностите или точност на прогнозата.</p></details>}

    </section>
    <section className="history-section"><h2>{exploratory?"Пробна прогноза":"Прогноза"}</h2><form method="get" className="model-controls"><input type="hidden" name="sport" value={model.sport}/><label>Известна величина<select name="input" value={input} onChange={e=>setInput(e.target.value)}><option value="minutes">Време, минути</option><option value="km">Дистанция, километри</option><option value="speed">Скорост, км/ч</option><option value="hr" disabled={!model.hr_model}>Пулс, уд./мин{!model.hr_model?" · няма връзка пулс–скорост":""}</option></select></label><label>Стойност<input name="value" type="number" min={limits[0]} max={limits[1]} step="any" value={value} onChange={e=>setValue(e.target.value)} required/></label><button className="action-button" disabled={!canPredict} aria-describedby="speed-prediction-help">Изчисли</button></form><p id="speed-prediction-help">{hrUnavailable?"За прогноза по пулс е нужна валидна връзка пулс–скорост. Избери време, дистанция или скорост.":!canPredict?<>Провери или <a href="#speed-tests">добави максимален тест</a> за {model.sport}, за да получиш индивидуална прогноза.</>:`Обхват: ${n(limits[0]!)}–${n(limits[1]!)} ${input==="minutes"?"минути":input==="km"?"км":input==="hr"?"уд./мин":"км/ч"}.`}</p>{["OUTSIDE_PREDICTION_RANGE","INVALID_PREDICTION_INPUT"].includes(model.prediction_error??"")&&<p role="alert">Стойността е извън допустимия обхват или е невалидна. Провери величината и стойността; формата и кривата остават достъпни.</p>}
      {canPredict&&model.prediction&&<dl className="model-prediction"><div><dt>Максимална продължителност</dt><dd>{time(model.prediction.duration_s)}</dd></div><div><dt>Скорост</dt><dd>{n(model.prediction.speed_kmh)} км/ч</dd></div><div><dt>Дистанция</dt><dd>{n(model.prediction.distance_m/1000)} км</dd></div><div><dt>Оценен пулс</dt><dd>{model.prediction.estimated_hr_bpm===null?"Извън наличната HR–скоростна база":`${n(model.prediction.estimated_hr_bpm)} уд/мин`}</dd></div></dl>}
      {canPredict&&model.prediction&&<p className="speed-prediction-evidence">{evidenceLabel(model.prediction,model)}{model.prediction.capped?" · достигнато ограничение на допълнителното отклонение":""}</p>}
      {canPredict&&model.prediction?.hr_prediction_source === "EXPERT_MIDPOINT" && <p role="status">Използван е ориентир по времевите граници за {model.prediction.zone}: индексът липсва или не съответства на допустимата продължителност.</p>}
      <p><strong>Максималната продължителност не е тренировъчна доза.</strong> Работата и интервалите се определят отделно според метода, приравнения обем Q, 7/40, Recovery и настройките.</p>
      {canPredict&&model.prediction&&["EXPERT_HISTORY","EXPERT_MINIMUM"].includes(model.prediction.hr_prediction_source??"")&&<p role="status">Връзката с пулса използва експертен времеви ориентир за зоната, защото няма подходящ измерен индекс.</p>}
      <p>Пулсът и скоростта са моделни ориентири от ТИ и допустимите продължителности по зони. {model.curve_metadata?"Историята определя мястото във времевия диапазон; при липса на данни се използва експертният минимум.":"При липсващ или неподходящ индекс се използва средата на експертния диапазон."} При кратки максимални усилия тази оценка не служи за дозиране. Дистанцията е еквивалент за равен терен.</p>
    </section>
    {Boolean(model.calibration_diagnostics?.residuals?.length)&&<section className="history-section"><details><summary>Как реалните тестове се сравняват с предварителната оценка?</summary><p>Реалните максимални тестове имат предимство и остават точни опори. Разликата показва какво е било оценено преди калибрацията; не осредняваме автоматично резултатите с предварителния модел.</p><div className="activity-table-wrap"><table><thead><tr><th>Време</th><th>Реален тест</th><th>Предварителна оценка</th><th>Разлика</th></tr></thead><tbody>{model.calibration_diagnostics!.residuals!.map((row,index)=><tr key={`${row.duration_s}-${index}`}><th>{manualClockTime(row.duration_s)}</th><td>{n(row.measured_speed_kmh)} км/ч</td><td>{n(row.prior_speed_kmh)} км/ч</td><td>{row.measured_vs_prior_percent>0?"+":""}{n(row.measured_vs_prior_percent)}%</td></tr>)}</tbody></table></div></details></section>}
    {model.functional_profile && <AthleteFunctionalProfile profile={model.functional_profile}/>}
    {model.hr_model && <HrSpeedZones model={model.hr_model} admission={model.index_admission} indexWindow={model.index_window} zoneSource={model.hr_zone_source}/>}
    <section className="history-section" id="speed-tests"><h2>Максимални тестове и контролни стартове</h2>
      {canEdit&&<div className="model-controls" role="group" aria-label="Източник на теста">
        <button type="button" className="action-button secondary" aria-pressed={testSource==="ACTIVITY"} onClick={()=>setTestSource("ACTIVITY")}>От записана активност</button>
        <button type="button" className="action-button secondary" aria-pressed={testSource==="MANUAL"} onClick={()=>setTestSource("MANUAL")}>Ръчен тест</button>
      </div>}
      {canEdit&&testSource==="MANUAL"?<ManualSpeedTestEditor key={`${editing?.entry_key??"new"}:${manualKey}`} model={model} test={editing} onReset={()=>{setEditing(undefined);setManualKey(k=>k+1);}}/>:<SpeedTestEditor model={model} canEdit={canEdit} activityRef={activityRef}/>}
      <h3>Записани тестове · {model.sport}</h3>
      {model.tests.some(t=>t.payload.sport!==model.sport)&&<p>Тестове за други спортове: {[...new Set(model.tests.filter(t=>t.payload.sport!==model.sport).map(t=>t.payload.sport))].sort().map(s=><Link key={s} href={`/speed?sport=${encodeURIComponent(s)}#speed-tests`} style={{marginRight:"1rem"}}>{s} ({model.tests.filter(t=>t.payload.sport===s).length})</Link>)}</p>}
      <p role="status">{message|| (pending?(arrived?"Изборът е запазен и моделът е обновен.":"Изборът е запазен. Обновяваме модела…"):"")}</p>
      {waiting&&<button type="button" onClick={()=>router.refresh()}>Обнови модела</button>}
      {!model.tests.some(t=>t.payload.sport===model.sport)?<p>Още няма записани тестове за този спорт.</p>:<div className="activity-table-wrap"><table>
        <thead><tr><th>Дата / тест</th><th>Време</th><th>Скорост</th><th>Участие в кривата</th><th>Източник</th></tr></thead>
        <tbody>{model.tests.filter(t=>t.payload.sport===model.sport).map(t=><tr key={t.entry_key}>
          <td>{t.payload.day}{t.payload.source==="MANUAL"?<><br/>{t.payload.name}</>:<><br/>{time(t.payload.start_s)}–{time(t.payload.start_s+t.payload.duration_s)}</>}
            {t.payload.test_mode==="EXPLORATORY"&&<p><strong>Пробен · {n(t.payload.coverage_percent??0)}% покритие</strong></p>}
          </td>
          <td>{manualClockTime(t.payload.duration_s)}</td><td>{n(t.payload.speed_kmh)} км/ч<br/><small>{t.payload.source==="MANUAL"?"Равен терен":"Vflat"}</small></td>
          <td><p>{t.payload.test_mode==="EXPLORATORY"||!t.payload.maximal?"Наблюдение · не е максимална опора":model.active_test_keys.includes(t.entry_key)?"Участва":!t.payload.enabled?"Изключен":"Не участва · провери датата или съпоставимостта"}</p>
            {canEdit&&t.payload.test_mode!=="EXPLORATORY"&&t.payload.maximal&&<button type="button" onClick={()=>toggle(t)} disabled={busy||waiting}>{t.payload.enabled?"Изключи от модела":"Включи в модела"}</button>}
          </td>
          <td>{t.payload.source==="MANUAL"?<><span>Ръчен тест</span>{canEdit&&<p><button type="button" disabled={busy||waiting} onClick={()=>{setEditing(t);setManualKey(k=>k+1);setTestSource("MANUAL");document.getElementById("speed-tests")?.scrollIntoView({behavior:"smooth"});}}>Редактирай</button></p>}</>:<Link href={`/activities/${t.payload.activity_ref}`}>Отвори активността</Link>}</td>
        </tr>)}</tbody></table></div>}
    </section>
    <section className="history-section"><h2>Критична скорост</h2>{model.critical_speed.speed_kmh!==undefined?<p><strong>{n(model.critical_speed.speed_kmh)} км/ч</strong> · D′ {n(model.critical_speed.d_prime_m||0)} м · {model.critical_speed.count} теста. {model.critical_speed.count===2?"Предварителна оценка: два теста не позволяват независима проверка на грешката.":`Средноквадратична грешка по дистанция: ${n(model.critical_speed.distance_rmse_m||0)} м.`}</p>:<p>Нужни са поне два избрани съпоставими теста между 2 и 20 минути с достатъчно различна продължителност. Препоръчително е да има и трети тест.</p>}<p>Оценка по Vflat или ръчно измерена скорост на равен терен и действителните тестови продължителности; референтните точки не участват.</p></section>
    {model.curve_metadata&&<section className="history-section"><h2>Как историята насочва времевите граници</h2>
      <p>{model.volume_history_basis==="HR_MEASURED"?"Използваме измерения обем по зони, за да определим мястото в експертните времеви граници.":model.volume_history_basis==="HR_PARTIAL"?"Има частични зонови данни. Предварителните времеви ориентири използват общото записано време, докато имаме достатъчна зонова история.":"При липса на измерен обем по зони общото време служи за предварителна експертна оценка. То не показва действително разпределение по зони."} {model.total_weekly_minutes!=null?`Записано време, приведено към седмица: ${n(model.total_weekly_minutes)} мин.`:""}</p>
      {model.preliminary_capacity&&<details><summary>Времеви ориентири по зони</summary><div className="activity-table-wrap"><table><thead><tr><th>Зона</th><th>Експертни граници</th><th>Предварителен ориентир</th><th>Основа на времето</th></tr></thead><tbody>{model.preliminary_capacity.anchors.map(anchor=><tr key={anchor.zone}><th>{anchor.zone}</th><td>{time(anchor.duration_min_s)} – {time(anchor.duration_max_s)}</td><td>{time(anchor.duration_s)}</td><td>{["TOTAL_VOLUME_EXPERT_POSITION","TOTAL_VOLUME_ESTIMATE"].includes(anchor.duration_source)?"Общ записан обем":anchor.duration_source==="EXPERT_MINIMUM"?"Експертен минимум":anchor.duration_source==="COACH_POSITION"?"Позиция от треньора":anchor.duration_source==="EXPERT_POSITION"?"Експертна позиция":"Обем по зони"}</td></tr>)}</tbody></table></div><p>Това са оценки за непрекъсната устойчивост, а не продължителности на тренировките. Реалните максимални тестове калибрират кривата и запазват точните си стойности.</p></details>}
    </section>}
    {!model.curve_metadata&&<section className="history-section"><h2>Донастройка чрез {model.volume_scope==="ALL_SPORTS"?"общия обем":"обема"} по зони</h2><p>Директно приравнено време {model.volume_scope==="ALL_SPORTS"?"от всички спортове":"от избрания спорт"} за {model.history_days} предходни календарни дни, приведено към седмица. Зоните се разполагат по експертната продължителност, независимо от ТИ. Корекцията се изглажда между зоните и изчезва в измерените тестови точки.</p><div className="activity-table-wrap"><table><thead><tr><th>Зона</th><th>Седмичен еквивалент</th><th>Заявена корекция на времето</th></tr></thead><tbody>{Object.entries(model.volume_weekly_min).map(([z,v])=><tr key={z}><th>{z}</th><td>{v===null?"Няма история":`${n(v)} мин`}</td><td>{n(100*model.zone_corrections[z])}%</td></tr>)}</tbody></table></div><p>{model.correction_applied_fraction===0?"Корекцията не е приложена — нужен е тест, или корекциите са неутрални.":`Приложена сила на корекцията: ${n(100*model.correction_applied_fraction)}%. При ограничение тя се намалява, за да остане кривата монотонна.`}</p></section>}
<p className="muted-copy">Моделът използва експертната референтна таблица и потвърдените максимални тестове от последните 90 дни. Обикновените тренировки и пробните записи не са максимални опори. Приложимостта на еднаква форма към различните спортове предстои да се калибрира.</p>
  </>;
}
