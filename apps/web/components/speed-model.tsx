"use client";
import Link from "next/link";
import {useState,type FormEvent} from "react";
import {useRouter} from "next/navigation";
import {saveModel,type SpeedModel,type SpeedTest} from "../lib/models";
const n=(v:number)=>new Intl.NumberFormat("bg-BG",{maximumFractionDigits:2}).format(v);
const time=(s:number)=>`${Math.floor(Math.round(s)/60)}:${String(Math.round(s)%60).padStart(2,"0")}`;
export function SpeedModelPanel({model,canEdit,activityRef}:{model:SpeedModel;canEdit:boolean;activityRef?:string}){
  const router=useRouter();
  const [busy,setBusy]=useState(false),[message,setMessage]=useState("");
  const [selected,setSelected]=useState(activityRef||model.activities.find(a=>a.sport===model.sport)?.activity_ref||"");
  const lo=Math.log(model.points[0]?.duration_s||10.8),hi=Math.log(model.points.at(-1)?.duration_s||43516);
  const vmax=Math.ceil((model.points[0]?.speed_kmh||40)/5)*5;
  const x=(t:number)=>48+852*(Math.log(t)-lo)/(hi-lo),y=(v:number)=>270-230*v/vmax;
  async function saveTest(event:FormEvent<HTMLFormElement>){
    event.preventDefault();setBusy(true);setMessage("");
    const f=new FormData(event.currentTarget),duration=Number(f.get("duration_s")),start=Number(f.get("start_s"));
    const previous=model.tests.find(t=>t.payload.activity_ref===selected&&t.payload.start_s===start&&t.payload.duration_s===duration);
    try{
      await saveModel("speed-test",{activity_ref:selected,start_s:start,duration_s:duration,maximal:f.get("maximal")==="on",comparable:f.get("comparable")==="on",enabled:true,use_for_cs:f.get("use_for_cs")==="on",conditions:String(f.get("conditions")),expected_revision:previous?.revision||0});
      setMessage("Тестът е запазен. Обновяваме индивидуалната крива.");router.refresh();
    }catch(e){setMessage(e instanceof Error?e.message:"Неуспешен запис.");}finally{setBusy(false);}
  }
  async function toggle(t:SpeedTest){
    setBusy(true);setMessage("");
    const p=t.payload;
    try{await saveModel("speed-test",{activity_ref:p.activity_ref,start_s:p.start_s,duration_s:p.duration_s,maximal:true,comparable:true,enabled:!p.enabled,use_for_cs:p.use_for_cs,conditions:p.conditions,expected_revision:t.revision});router.refresh();}
    catch(e){setMessage(e instanceof Error?e.message:"Неуспешен запис.");}finally{setBusy(false);}
  }
  return <>
    <section className="history-section"><form method="get" className="model-controls"><label>Спорт<select name="sport" defaultValue={model.sport}>{[...new Set([model.sport,...model.sports])].map(s=><option key={s}>{s}</option>)}</select></label><button className="action-button secondary">Покажи</button></form>
      <p>{model.status==="CALIBRATED"?`Индивидуална крива · ${model.active_test_count} максимални теста` :"Референтна крива · добавете максимален тест за индивидуална прогноза"}</p>
      {model.warnings.includes("CONFLICTING_TESTS")&&<p role="alert">Избраните тестове си противоречат. Изключете несъпоставимия тест; индивидуалните прогнози са спрени.</p>}
      {model.warnings.includes("INCOMPARABLE_MODEL_VERSIONS")&&<p role="alert">Тестовете използват различни версии на Vflat. Изключете или преизчислете старите тестове, преди да ги сравнявате.</p>}
      <figure className="history-chart"><svg viewBox="0 0 920 320" role="img" aria-label="Средна максимална скорост според продължителността">
        {[0,.25,.5,.75,1].map(f=><g key={f}><line x1="48" x2="900" y1={y(vmax*f)} y2={y(vmax*f)} stroke="currentColor" opacity=".12"/><text x="40" y={y(vmax*f)+4} textAnchor="end" fill="currentColor" fontSize="12">{n(vmax*f)}</text></g>)}
        <polyline points={model.points.map(p=>`${x(p.duration_s)},${y(p.speed_kmh)}`).join(" ")} fill="none" stroke="var(--accent,#41b88c)" strokeWidth="3"/>
        {model.tests.filter(t=>model.active_test_keys.includes(t.entry_key)).map(t=><circle key={t.entry_key} cx={x(t.payload.duration_s)} cy={y(t.payload.speed_kmh)} r="5" fill="#ef9c45"><title>{t.payload.day} · {time(t.payload.duration_s)} · {n(t.payload.speed_kmh)} км/ч</title></circle>)}
        {[60,180,720,3600,21600].filter(t=>Math.log(t)>=lo&&Math.log(t)<=hi).map(t=><text key={t} x={x(t)} y="297" textAnchor="middle" fill="currentColor" fontSize="12">{t/60} мин</text>)}
        <text x="48" y="20" fill="currentColor" fontSize="12">км/ч · Vflat</text>
      </svg><figcaption>Времето е по логаритмична скала. Точките са измерени тестове. Формата извън тях е оценка от референтния модел.</figcaption></figure>
    </section>
    <section className="history-section"><h2>Прогноза</h2><form method="get" className="model-controls"><input type="hidden" name="sport" value={model.sport}/><label>Известна величина<select name="input"><option value="minutes">Време, минути</option><option value="km">Дистанция, километри</option><option value="speed">Скорост, км/ч</option></select></label><label>Стойност<input name="value" type="number" min=".01" step="any" defaultValue="3" required/></label><button className="action-button" disabled={model.status!=="CALIBRATED"}>Изчисли</button></form>
      {model.prediction&&<dl className="model-prediction"><div><dt>Продължителност</dt><dd>{time(model.prediction.duration_s)}</dd></div><div><dt>Скорост</dt><dd>{n(model.prediction.speed_kmh)} км/ч</dd></div><div><dt>Дистанция</dt><dd>{n(model.prediction.distance_m/1000)} км</dd></div><div><dt>Оценен пулс</dt><dd>{model.prediction.estimated_hr_bpm===null?"Извън наличната HR–скоростна база":`${n(model.prediction.estimated_hr_bpm)} уд/мин`}</dd></div></dl>}
      <p>Пулсът се оценява от индекса на тренираност в диапазона с налични данни. При кратки максимални усилия тази оценка не служи за дозиране. Дистанцията е еквивалент за равен терен.</p>
    </section>
    <section className="history-section"><h2>Критична скорост</h2>{model.critical_speed.speed_kmh!==undefined?<p><strong>{n(model.critical_speed.speed_kmh)} км/ч</strong> · D′ {n(model.critical_speed.d_prime_m||0)} м · {model.critical_speed.count} теста. {model.critical_speed.count===2?"Предварителна оценка: два теста не позволяват независима проверка на грешката.":`Средноквадратична грешка по дистанция: ${n(model.critical_speed.distance_rmse_m||0)} м.`}</p>:<p>Нужни са поне два избрани съпоставими теста между 2 и 20 минути с достатъчно различна продължителност. Препоръчително е да има и трети тест.</p>}<p>Оценка по Vflat и действителните тестови продължителности; референтните точки не участват.</p></section>
    <section className="history-section"><h2>Донастройка чрез обема по зони</h2><p>Директно приравнено време за {model.history_days} предходни календарни дни, приведено към седмица. Корекцията се изглажда между зоните и изчезва в измерените тестови точки.</p><div className="activity-table-wrap"><table><thead><tr><th>Зона</th><th>Седмичен еквивалент</th><th>Заявена корекция на времето</th></tr></thead><tbody>{Object.entries(model.volume_weekly_min).map(([z,v])=><tr key={z}><th>{z}</th><td>{v===null?"Няма история":`${n(v)} мин`}</td><td>{n(100*model.zone_corrections[z])}%</td></tr>)}</tbody></table></div><p>{model.correction_applied_fraction===0?"Корекцията не е приложена — нужни са тест и подходяща HR–скоростна база, или корекциите са неутрални.":`Приложена сила на корекцията: ${n(100*model.correction_applied_fraction)}%. При ограничение тя се намалява, за да остане кривата монотонна.`}</p></section>
    <section className="history-section"><h2>Максимални тестове и контролни стартове</h2><p>Изберете един непрекъснат максимален участък без загряване и разпускане. Времето в пулсовите зони не се използва като продължителност на теста.</p>
      {canEdit&&<form onSubmit={saveTest} className="model-test-form"><label>Активност<select value={selected} onChange={e=>setSelected(e.target.value)} required><option value="">Изберете активност</option>{model.activities.map(a=><option key={a.activity_ref} value={a.activity_ref}>{a.day} · {a.name} · {a.sport}</option>)}</select></label><div className="model-controls"><label>Начало от записа, секунди<input name="start_s" type="number" min="0" step="1" defaultValue="0" required/></label><label>Продължителност, секунди<input name="duration_s" type="number" min="11" max="43516" step="1" defaultValue="720" required/></label></div><label>Условия и съпоставимост<input name="conditions" minLength={3} maxLength={400} placeholder="Напр. равен терен, същите ролки, без спирания" required/></label><label><input type="checkbox" name="maximal" required/> Максимално непрекъснато усилие</label><label><input type="checkbox" name="comparable" required/> Условията са съпоставими с другите избрани тестове</label><label><input type="checkbox" name="use_for_cs"/> Използвай и за критична скорост</label><button className="action-button" disabled={busy}>Запази тестовия участък</button></form>}
      <p role="status">{message}</p><div className="activity-table-wrap"><table><thead><tr><th>Дата / спорт</th><th>Време</th><th>Vflat</th><th>Състояние</th><th>Активност</th></tr></thead><tbody>{model.tests.map(t=><tr key={t.entry_key}><td>{t.payload.day} · {t.payload.sport}</td><td>{time(t.payload.duration_s)}</td><td>{n(t.payload.speed_kmh)} км/ч</td><td>{canEdit?<button type="button" onClick={()=>toggle(t)} disabled={busy}>{t.payload.enabled?"Изключи от модела":"Включи в модела"}</button>:t.payload.enabled?"Избран":"Изключен"}</td><td><Link href={`/activities/${t.payload.activity_ref}`}>Отвори</Link></td></tr>)}</tbody></table></div>
    </section><p className="muted-copy">Моделът използва експертната референтна таблица и личните тестове от последните 90 дни. Приложимостта на еднаква форма към различните спортове предстои да се калибрира.</p>
  </>;
}
