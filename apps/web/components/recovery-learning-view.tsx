"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState, type CSSProperties } from "react";
import { LEARNING_MODE_LABELS } from "../lib/individual-learning";
import { componentColor, componentLabel } from "../lib/training-visuals";
import { LEARNING_COMPONENTS, learningChange, learningDate, learningNumber, type ComponentLearning, type LearningComponent, type LearningDay, type RecoveryLearningView } from "../lib/recovery-learning";

const panelSignature = (row: LearningDay) => JSON.stringify([row.stress_basis,row.stress_reference,Object.entries(row.stress_weights).sort(([a],[b])=>a.localeCompare(b))]);

/** Gaps and changed panels break paths; neither implies a physiological trend. */
export function learningPaths(rows: LearningDay[], value: "stress" | "baseline", x: (index: number) => number, y: (value: number) => number) {
  const paths: string[] = [];
  let path = "";
  rows.forEach((row,index) => {
    const previous = rows[index-1];
    const gap = previous && (Date.parse(`${row.day}T00:00:00Z`)-Date.parse(`${previous.day}T00:00:00Z`) > 86_400_000 || panelSignature(previous)!==panelSignature(row));
    if (row[value] === null || gap) { if (path) paths.push(path); path = ""; }
    if (row[value] !== null) path += `${path ? "L" : "M"}${x(index)},${y(row[value])} `;
  });
  if (path) paths.push(path);
  return paths;
}

export function learningLoadSegments(row: LearningDay, selected: LearningComponent | null = null) {
  let total = 0;
  return LEARNING_COMPONENTS.filter(key=>selected===null || key===selected).flatMap(key=> {
    const value = row.actual_q_by_component[key];
    if (value===null) return [];
    const segment = { component:key, value, start:total, end:total+value };
    total += value;
    return [segment];
  });
}

function Timeline({ rows, kind }: { rows: LearningDay[]; kind: "load" | "stress" }) {
  const holder = useRef<HTMLDivElement>(null);
  const [width,setWidth] = useState(600);
  const [selected,setSelected] = useState<LearningComponent | null>(null);
  const id = useId();
  useEffect(() => {
    const node = holder.current;
    if (!node) return;
    const measure = () => setWidth(Math.max(250,node.getBoundingClientRect().width));
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure); observer.observe(node);
    return () => observer.disconnect();
  },[]);
  const title = kind === "load" ? "Реално изпълнено натоварване" : "Интегрален стрес";
  const present = rows.filter(row => kind === "load" ? LEARNING_COMPONENTS.some(key=>row.actual_q_by_component[key]!==null) : row.stress !== null);
  const left=48, right=width-14, top=24, bottom=174, height=216;
  const start = rows[0] ? Date.parse(`${rows[0].day}T00:00:00Z`) : 0;
  const span = Math.max(86_400_000, rows.length ? Date.parse(`${rows.at(-1)!.day}T00:00:00Z`)-start : 0);
  const x = (index:number) => left+5+(Date.parse(`${rows[index].day}T00:00:00Z`)-start)/span*(right-left-10);
  const maximum = kind === "load" ? Math.max(1,...rows.map(row=>learningLoadSegments(row,selected).at(-1)?.end ?? 0)) : 100;
  const y = (value:number) => bottom-value/maximum*(bottom-top);
  const ticks = rows.length ? [...new Set([0,Math.floor((rows.length-1)/2),rows.length-1])] : [];
  const barWidth = Math.max(1,Math.min(14,(right-left)/Math.max(1,span/86_400_000+1)*.72));
  return <figure className="learning-chart"><figcaption id={`${id}-title`}><strong>{title}</strong><span>{kind === "load" ? "Q · еквивалентни минути за деня" : "Стрес · точки от 0 до 100"}</span></figcaption>
    {kind==="load"&&<div className="learning-zone-key" role="group" aria-label="Компоненти в графиката"><button type="button" aria-pressed={selected===null} onClick={()=>setSelected(null)}>Всички</button>{LEARNING_COMPONENTS.map(key=><button key={key} type="button" aria-pressed={selected===key} onClick={()=>setSelected(selected===key?null:key)} style={{"--component-color":componentColor(key)} as CSSProperties}><i aria-hidden="true"/>{componentLabel(key)}</button>)}</div>}
    <div ref={holder}>{present.length ? <svg role="img" aria-labelledby={`${id}-title ${id}-description`} viewBox={`0 0 ${width} ${height}`} width="100%" height={height}>
      <desc id={`${id}-description`}>{kind === "load" ? `Дневно Q по Z1, Z2, Z3, Z4, Z5 и сила. ${selected?`Показан е само компонент ${componentLabel(selected)}.`:"Цветовете показват състава на натоварването."} Непълните разбивки са отбелязани.` : "Един претеглен индекс от независими наблюдения. Пунктирът е личната база. Прекъсванията означават липсващи данни или промяна на набора показатели."}</desc>
      {[0,.5,1].map(f => <g key={f}><line x1={left} y1={y(maximum*f)} x2={right} y2={y(maximum*f)} className="learning-grid"/><text x={left-9} y={y(maximum*f)+4} textAnchor="end">{learningNumber(maximum*f)}</text></g>)}
      {ticks.map(index => <text key={index} x={x(index)} y={bottom+25} textAnchor={index===0?"start":index===rows.length-1?"end":"middle"}>{rows[index].day.slice(8,10)}.{rows[index].day.slice(5,7)}</text>)}
      {kind === "load" ? rows.map((row,index)=> {
        const segments = learningLoadSegments(row,selected);
        const complete = LEARNING_COMPONENTS.every(key=>row.actual_q_by_component[key]!==null);
        const total = segments.at(-1)?.end ?? 0;
        if (!segments.length) return <line key={row.day} x1={x(index)-2} x2={x(index)+2} y1={bottom+5} y2={bottom+5} className="learning-missing-mark"><title>{`${learningDate(row.day)}: няма компонентни данни`}</title></line>;
        return <g key={row.day}>{segments.map(segment=><rect key={segment.component} data-component={segment.component} x={x(index)-barWidth/2} y={y(segment.end)} width={barWidth} height={Math.max(0,y(segment.start)-y(segment.end))} fill={componentColor(segment.component)}><title>{`${learningDate(row.day)} · ${componentLabel(segment.component)}: ${learningNumber(segment.value)} Q${complete?"":" · непълна дневна разбивка"}`}</title></rect>)}
          {total===0&&<circle cx={x(index)} cy={bottom} r="2" fill={selected?componentColor(selected):"var(--text-muted)"}><title>{`${learningDate(row.day)}: 0 Q${complete?"":" · непълна дневна разбивка"}`}</title></circle>}
          {!complete&&<line x1={x(index)-barWidth/2} x2={x(index)+barWidth/2} y1={y(total)-4} y2={y(total)-4} className="learning-missing-mark"><title>{`${learningDate(row.day)}: непълна дневна разбивка`}</title></line>}
        </g>;
      }) : <>
        {learningPaths(rows,"baseline",x,y).map((d,index)=><path key={`base-${index}`} d={d} className="learning-reference"/>)}
        {learningPaths(rows,"stress",x,y).map((d,index)=><path key={`stress-${index}`} d={d} className="learning-stress-line"/>)}
        {rows.map((row,index)=>row.stress!==null&&<circle key={row.day} cx={x(index)} cy={y(row.stress)} r="2.5" className="learning-stress-mark"><title>{`${learningDate(row.day)}: ${learningNumber(row.stress)} точки${row.baseline!==null?`; лична база ${learningNumber(row.baseline)}`:""}${Object.keys(row.stress_weights).length?`; дялове в измерения набор: ${Object.entries(row.stress_weights).map(([key,weight])=>`${key} ${learningNumber(weight)}%`).join(", ")}`:"; няма данни за теглата"}`}</title></circle>)}
      </>}
    </svg> : <p className="learning-empty">{kind==="load"?"Няма годни измерени данни с компонентна разбивка на натоварването през този период.":"Няма достатъчно независими наблюдения за графика на стреса."}</p>}</div>
    <p className="learning-caption">{kind==="load" ? `${selected?`Показан е само ${componentLabel(selected)}. `:""}Цветовете показват реалните акценти. Липсваща разбивка не е нулево натоварване; чертичката обозначава непълни данни.` : "Плътна линия: един претеглен индекс · пунктир: лична база · прекъсване: липсваща оценка или променен набор показатели."}</p>
  </figure>;
}

const validationLabel:Record<string,string> = { WARMUP:"Натрупване на наблюдения",PASSED:"Проверени прогнози",FAILED:"Нужна е допълнителна проверка" };
const responseLabel:Record<ComponentLearning["response_status"],string> = { SUPPORTED_POSITIVE:"Положителна връзка",SUPPORTED_NEGATIVE:"Отрицателна връзка",UNCERTAIN:"Неопределена връзка",INSUFFICIENT_DATA:"Събира данни" };
const actionLabel:Record<ComponentLearning["action_status"],string> = { COLLECTING:"Нужни са още наблюдения", SELECTED:"Избрана следваща промяна", WAITING:"Изчаква текущото проследяване", NOT_ELIGIBLE:"Не е избран за текущия период" };
const basisLabel:Record<ComponentLearning["management_basis"],string> = { POLICY_REDUCTION:"Правило за намаляване",EXPLORATION:"Проучване",MODEL_SUPPORTED:"Подкрепено от модела",HOLD:"Запазване на текущата доза" };
const quantity = (value:number|null) => value===null?"—":learningNumber(value);

function ComponentAssessment({ item }: { item: ComponentLearning }) {
  const selected = item.action_status==="SELECTED";
  return <article className={`learning-component${selected?" is-selected":""}`} style={{"--component-color":componentColor(item.component)} as CSSProperties} aria-labelledby={`component-${item.component}`}>
    <header><h3 id={`component-${item.component}`}><i aria-hidden="true"/>{componentLabel(item.component)}</h3><span className="learning-response-label">{responseLabel[item.response_status]}</span></header>
    <dl className="learning-component-load"><div><dt>7 дни · Q/седм.</dt><dd>{quantity(item.recent_weekly_q)}</dd></div><div><dt>40 дни · Q/седм.</dt><dd>{quantity(item.reference_weekly_q)}</dd></div><div><dt>Акцент 7/40</dt><dd>{item.accent_ratio===null?"—":`${learningNumber(item.accent_ratio)}×`}</dd></div></dl>
    <p className="learning-model-response">{item.predicted_performance_percent===null?"Още няма проверена оценка на реакцията.":<>Сравнение при +{item.test_step_percent}% Q: <strong>{learningChange(item.predicted_performance_percent)}</strong> {item.model_scope==="GLOBAL"?"обща работоспособност":"работоспособност"}.</>}</p>
    {selected?<div className="learning-component-decision" aria-label={`Следваща промяна ${componentLabel(item.component)}`}><span>{basisLabel[item.management_basis]}</span><dl><div><dt>Предложена промяна</dt><dd>{item.proposed_change_percent===null?"—":learningChange(item.proposed_change_percent)}</dd></div><div><dt>Допуснато от модела</dt><dd>{item.applied_change_percent===null?"—":learningChange(item.applied_change_percent)}</dd></div></dl></div>:<p className="learning-action-label">{actionLabel[item.action_status]}</p>}
    <details className="learning-component-details"><summary>Основание за {componentLabel(item.component)}</summary><p>{item.reason}</p><p>{item.evidence_count} съпоставими резултата · {item.isolated_changes} отделими промени · {validationLabel[item.validation_status]??"Оценката се проверява"}.</p><p>Покритие на реалното Q: {item.recent_observed_days}/7 и {item.reference_observed_days}/40 дни. „—“ означава непълна база.</p>
      {selected&&item.baseline_actual_weekly_q!==null&&<p>База на съпоставимия блок: {learningNumber(item.baseline_actual_weekly_q)} Q/седм.{item.target_weekly_q!==null?` → цел ${learningNumber(item.target_weekly_q)} Q/седм.`:""}{item.execution_weekly_q_ceiling!==null?` · текущ таван ${learningNumber(item.execution_weekly_q_ceiling)} Q/седм.`:""} Това е отделна база от текущото 7/40.</p>}
      {selected&&item.effective_from&&<p>Следващ период: {learningDate(item.effective_from)}{item.expires_on?` – ${learningDate(item.expires_on)}`:""}.</p>}
      {item.predicted_performance_percent!==null&&<><p>Моделен интервал: {learningChange(item.predicted_performance_low!)} до {learningChange(item.predicted_performance_high!)}. Обхват: {item.model_scope==="GLOBAL"?"обща работоспособност":componentLabel(item.model_scope??item.component)}.</p><p>Стандартното сравнение при +5% Q описва проверена връзка с резултата, а не доказана причинност или отделна препоръка.</p></>}
    </details>
  </article>;
}

function recoveryObservationLabel(status: string) {
  const labels: Record<string,string> = { NO_ELEVATED_RESPONSE:"Няма отчетено повишение на стреса", INSUFFICIENT_DATA:"Недостатъчни наблюдения", CONTEXT_REVIEW:"Контекст за отделен преглед", DELAYED:"Няма потвърдено връщане в експертния срок", OBSERVING:"Проследяването продължава" };
  return labels[status]??"Няма оценка на връщането";
}

export function RecoveryLearning({ view }: { view: RecoveryLearningView }) {
  const episodes = [...view.episodes].sort((a,b)=>b.start.localeCompare(a.start));
  const recent = episodes.slice(0,5);
  const recoveryMax = Math.max(1,...recent.flatMap(item=>[item.recovery_limit_days,item.recovery_days??0]));
  const latestStress = [...view.series].reverse().find(row=>row.stress!==null);
  const last = latestStress ?? view.series.at(-1);
  const panelLabel = last?.stress_basis==="FROZEN_BLOCK"?"Един и същ набор за проследения блок":last?.stress_basis==="FIXED_PANEL"?"Постоянен набор за периода":"Няма наличен набор показатели";
  return <div className="learning-content">
    <section className="learning-panel learning-current" aria-labelledby="learning-current-heading"><div className="learning-heading"><h2 id="learning-current-heading">{LEARNING_MODE_LABELS[view.mode]}</h2><span>{view.evidence_count} съпоставими {view.evidence_count===1?"наблюдение":"наблюдения"}</span></div><p className="learning-summary">{view.summary}</p>
      {view.mode==="SHADOW"&&<p className="learning-caption">Предложенията се проследяват в „Наблюдение“ и още не променят тренировъчната доза.</p>}
      {view.mode==="CONTROL"&&<p className="learning-caption">Генераторът може да намали допуснатата корекция според периода, готовността и метода. Фиксираната програма изисква одобрение.</p>}
      {view.evidence_count===0&&<Link className="learning-observation-link" href="/response">Към наблюденията за стрес и възстановяване →</Link>}
    </section>
    <section className="learning-panel" aria-label="Натоварване и независима реакция"><div className="learning-heading"><h2>Натоварване и реакция</h2><span>{view.series.length?`${learningDate(view.series[0].day)} – ${learningDate(view.series.at(-1)!.day)}`:"Няма наблюдаван период"}</span></div><div className="learning-charts"><Timeline rows={view.series} kind="load"/><Timeline rows={view.series} kind="stress"/></div><p className="learning-caption">Стресът съчетава независимите показатели за състоянието с тегла. Той не се изчислява от тренировъчното Q.</p></section>
    <section className="learning-panel" aria-labelledby="learning-components-heading"><div className="learning-heading"><h2 id="learning-components-heading">Как реагираш на всеки компонент</h2><span>Една следваща промяна за проследяване</span></div><p className="learning-caption">Акцентът сравнява реалното Q с твоята собствена история. Оценката на реакцията изисква независими резултати.</p><div className="learning-component-grid">{view.components.map(item=><ComponentAssessment key={item.component} item={item}/>)}</div>
      {Number(view.observations.reaction_episodes)>0&&view.observations.performance_observations===0&&<p className="learning-caption">Наблюдаваната реакция се отчита. Още няма съпоставим резултат за оценка на тренировъчния ефект.</p>}
    </section>
    <details className="learning-panel learning-details"><summary>Наблюдения и основания</summary>
      <h3>Тегла на интегралния стрес</h3><p>{latestStress?`Последна оценка на стреса · ${learningDate(latestStress.day)}. `:""}{panelLabel}{!latestStress&&last?` · ${learningDate(last.day)}`:""}. {last?.stress===null?"За този ден липсва пълен набор за оценка на стреса.":""}</p><p className="learning-caption">Номиналните тегла задават относителната важност. Ефективните тегла са нормализирани до 100% само за избрания набор; те не са процент увереност. Липсващ показател от него оставя деня без оценка.</p>
      <div className="learning-table-wrap"><table><caption>Показатели и тегла{last?` за ${learningDate(last.day)}`:""}</caption><thead><tr><th>Показател</th><th>Номинално тегло</th><th>Ефективно тегло</th><th>Дни с данни</th></tr></thead><tbody>{view.channels.map(item=><tr key={item.key}><th scope="row">{item.label}</th><td>{item.nominal_weight===null?"—":`${learningNumber(item.nominal_weight)} т.`}</td><td>{last?.stress_weights[item.key]===undefined?"—":`${learningNumber(last.stress_weights[item.key])}%`}</td><td>{item.observed_days}</td></tr>)}</tbody></table></div>
      <h3>Време за възстановяване</h3>{recent.length?<><div className="learning-recovery-key"><span><i className="learning-reference-swatch"/>Експертен срок</span><span><i className="learning-observed-swatch"/>Наблюдавано връщане</span><span>Дни</span></div><div className="learning-recovery-bars">{recent.map(item=><div className="learning-recovery-row" key={item.id}><span>{learningDate(item.start)}<small>{item.loading_days} дни натоварване</small></span><div><div className="learning-recovery-track"><span className="learning-reference-bar" style={{width:`${100*item.recovery_limit_days/recoveryMax}%`}}/><span>{learningNumber(item.recovery_limit_days)} дни</span></div><div className="learning-recovery-track">{item.recovery_days!==null?<><span className="learning-observed-bar" style={{width:`${100*item.recovery_days/recoveryMax}%`}}/><span>{learningNumber(item.recovery_days)} дни</span></>:<span className="learning-caption">{recoveryObservationLabel(item.status)}</span>}</div></div></div>)}</div></>:<p className="learning-empty">Няма завършен съпоставим блок с достатъчно данни за възстановяването.</p>}
      {episodes.length>0&&<div className="learning-table-wrap"><table><caption>Проследени блокове</caption><thead><tr><th>Блок</th><th>Връщане / срок</th><th>Работоспособност</th><th>Основание</th></tr></thead><tbody>{episodes.map(item=><tr key={item.id}><td>{learningDate(item.start)} – {learningDate(item.load_end)}</td><td>{item.recovery_days===null?recoveryObservationLabel(item.status):`${learningNumber(item.recovery_days)} дни`} / {learningNumber(item.recovery_limit_days)} дни</td><td>{item.performance_change_percent===null?"Няма съпоставим резултат":learningChange(item.performance_change_percent)}</td><td>{item.reason}</td></tr>)}</tbody></table></div>}
      <details><summary>Числови данни от графиките</summary><div className="learning-table-wrap"><table><thead><tr><th>Дата</th>{LEARNING_COMPONENTS.map(key=><th key={key}>{componentLabel(key)} · Q</th>)}<th>Общо Q</th><th>Стрес</th><th>Лична база</th></tr></thead><tbody>{view.series.map(row=><tr key={row.day}><td>{learningDate(row.day)}</td>{LEARNING_COMPONENTS.map(key=><td key={key}>{quantity(row.actual_q_by_component[key])}</td>)}<td>{quantity(row.actual_q)}</td><td>{quantity(row.stress)}</td><td>{quantity(row.baseline)}</td></tr>)}</tbody></table></div></details>
    </details>
    <details className="learning-panel learning-details"><summary>Как се взема решението</summary><p>Моделът използва изпълненото натоварване, независимите наблюдения и съпоставими резултати. При 7, 14 и 21 дни натоварване експертният срок е съответно 3, 5 и 7 дни; междинните стойности се изчисляват плавно.</p><p>Само един компонент може да има избрана следваща промяна с до 20% спрямо реалната база на съпоставим блок. Сравненията при +5% Q са оценки на модела, а не шест едновременни препоръки. Разтоварването се определя отделно; обновяването не натрупва промени.</p><p>Сроковете са експертни настройки за управление и не определят медицинска диагноза. Бързото възстановяване само по себе си не доказва тренировъчно подобрение.</p>{view.limitations.length>0&&<ul>{view.limitations.map((item,index)=><li key={index}>{item}</li>)}</ul>}<Link href="/planning">Настройки на планирането →</Link></details>
  </div>;
}
