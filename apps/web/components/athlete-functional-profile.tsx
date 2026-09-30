import type {FunctionalProfile} from "../lib/models";
import {manualClockTime as clockTime} from "../lib/speed-tests";

const number=(value:number)=>new Intl.NumberFormat("bg-BG",{maximumFractionDigits:1}).format(value);
const signed=(value:number)=>`${value>0?"+":""}${number(value)}%`;
const orientation={
  LONGER_DURATION_ADVANTAGE:"Относително предимство при по-дългите усилия",
  SHORTER_DURATION_ADVANTAGE:"Относително предимство при по-кратките усилия",
  NO_RELATIVE_DIFFERENCE:"Сходно отклонение при кратките и дългите усилия",
};

export function AthleteFunctionalProfile({profile}:{profile:FunctionalProfile}){
  if(profile.status==="UNAVAILABLE")return null;
  const shape=profile.shape;
  const window=shape?.test_duration_range_s;
  const supported=shape?.status==="TEST_SUPPORTED_WINDOW"&&shape.orientation!==null;
  return <section className="history-section speed-functional-profile" aria-label="Скоростно-издръжливостен профил">
    <h2>Твоят скоростно-издръжливостен профил</h2>
    <p>Сравняваме общото ниво и формата на индивидуалната крива поотделно. Профилът описва текущите резултати за {window?`${clockTime(window[0])} – ${clockTime(window[1])}`:"наличните продължителности"}.</p>
    <div className="speed-profile-facts">
      {profile.overall_level&&<div><span>Общо ниво спрямо нормативната крива</span><strong>{signed(profile.overall_level.difference_percent)}</strong><small>{profile.overall_level.basis==="REAL_TESTS"?"По реалните максимални тестове":"Предварителна оценка по модела"}</small></div>}
      <div><span>Форма след отделяне на общото ниво</span><strong>{supported?orientation[shape.orientation!]:"Нужни са още различни максимални тестове"}</strong><small>{supported?"Само в диапазона между реалните тестове · предварителен прочит":"Един тест определя ниво, но не доказва индивидуална форма."}</small></div>
    </div>
    {supported&&<p>Това предимство е относително спрямо нормативната форма. Дали е приоритет за развитие зависи от целевата дисциплина.</p>}
    {profile.test_points.length>0&&<details><summary>Какво показват реалните тестове?</summary>
      <div className="activity-table-wrap"><table><thead><tr><th>Продължителност</th><th>Измерена скорост</th><th>Спрямо нормативната крива</th><th>След отделяне на общото ниво</th></tr></thead><tbody>{profile.test_points.map((point,index)=><tr key={`${point.duration_s}-${index}`}><th>{clockTime(point.duration_s)}</th><td>{number(point.speed_kmh)} км/ч</td><td>{signed(point.difference_percent)}</td><td>{supported?signed(point.shape_difference_percent):"Няма достатъчно тестове"}</td></tr>)}</tbody></table></div>
      <p>Прогнозите извън тестовете и ограничението от 5% не служат като измерено доказателство за силна или слаба страна.</p>
    </details>}
    <details><summary>Връзка с тренировъчната история</summary>
      {profile.training_context.zones.some(zone=>zone.weekly_minutes!==null)?<><p>{profile.training_context.source==="HR_MEASURED"?"Обем от измерени пулсови зони.":profile.training_context.source==="HR_PARTIAL"?"Измерените зонови данни са непълни. Липсващите стойности остават неизвестни.":"Зоновият обем включва оценки; те не са измерено разпределение по пулс."}</p><div className="activity-table-wrap"><table><thead><tr><th>Зона</th><th>Седмичен еквивалентен обем</th></tr></thead><tbody>{profile.training_context.zones.map(zone=><tr key={zone.zone}><th>{zone.zone}</th><td>{zone.weekly_minutes===null?"Няма данни":`${number(zone.weekly_minutes)} мин Q`}</td></tr>)}</tbody></table></div><p>По-малкият обем и по-слабият резултат са повод за проверка. Повторни съпоставими тестове след тренировъчен блок показват как се променя профилът.</p></>:<p>Все още няма надеждно измерено разпределение по зони за това сравнение.</p>}
      <p>Историята сама по себе си не доказва причината за разликите. Профилът не определя генотип, мускулни влакна или предел на развитие.</p>
    </details>
  </section>;
}
