"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState, type CSSProperties } from "react";
import { LEARNING_MODE_LABELS } from "../lib/individual-learning";
import { componentColor, componentLabel } from "../lib/training-visuals";
import { LEARNING_COMPONENTS, learningChange, learningDate, learningNumber, type ComponentLearning, type LearningComponent, type LearningDay, type RecoveryLearningView } from "../lib/recovery-learning";
import { CHANNEL_LABELS } from "../lib/response-monitoring";

const panelSignature = (row: LearningDay) => JSON.stringify([row.stress_basis,row.stress_reference,row.stress_quality,row.history_model_version,Object.entries(row.stress_weights).sort(([a],[b])=>a.localeCompare(b))]);

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

/** Canonical 7/40 values arrive from the same load history as the load-history view. */
export function learningIndexPaths(rows: LearningDay[], component: LearningComponent, estimated: boolean, x: (index: number) => number, y: (value: number) => number) {
  const paths: string[] = [];
  let path = "";
  rows.forEach((row,index)=> {
    const value=row.index_7_40_by_component[component];
    const gap=index>0 && Date.parse(`${row.day}T00:00:00Z`)-Date.parse(`${rows[index-1].day}T00:00:00Z`)>86_400_000;
    if (value===null || (row.index_load_status!=="COMPLETE")!==estimated || gap) { if(path)paths.push(path);path=""; }
    if(value!==null && (row.index_load_status!=="COMPLETE")===estimated) path+=`${path?"L":"M"}${x(index)},${y(value)} `;
  });
  if(path)paths.push(path);
  return paths;
}

export function learningQ3Paths(rows: LearningDay[], component: LearningComponent, estimated: boolean, x: (index:number)=>number, y: (value:number)=>number) {
  const projected=rows.map(row=>({...row,index_7_40_by_component:row.q3_by_component,index_load_status:(row.q3_partial||row.q3_observed_days[component]!==3?"PARTIAL":"COMPLETE") as LearningDay["index_load_status"]}));
  return learningIndexPaths(projected,component,estimated,x,y);
}

const loadStatusLabel:Record<LearningDay["load_status"],string> = {
  COMPLETE:"Пълно отчетено натоварване", MODELLED:"Натоварване с начална моделна оценка", PARTIAL:"Частично отчетено натоварване",
  UNAVAILABLE:"Няма потвърдено покритие на натоварването", REST:"Покрит ден без отчетена тренировка",
};

function Timeline({ rows, decision, decisionReason, selection, onSelection, showTotal, onTotal, accents }: {
  rows: LearningDay[]; decision: ComponentLearning | undefined; decisionReason: string;
  selection:LearningComponent[]; onSelection:(value:LearningComponent[])=>void; showTotal:boolean; onTotal:(value:boolean)=>void; accents:LearningComponent[];
}) {
  const holder = useRef<HTMLDivElement>(null);
  const [width,setWidth] = useState(600);
  const selected=selection.length===1?selection[0]:null;
  const [selectedIndex,setSelectedIndex] = useState<number | null>(null);
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
  const components=LEARNING_COMPONENTS.filter(key=>selection.includes(key));
  const present=rows.some(row=>row.stress!==null || components.some(key=>row.q3_by_component[key]!==null));
  const hasIndex=rows.some(row=>components.some(key=>row.q3_by_component[key]!==null));
  const hasStress=rows.some(row=>row.stress!==null);
  const left=44, right=width-42, top=34, bottom=224, height=266;
  const start=rows[0]?Date.parse(`${rows[0].day}T00:00:00Z`):0;
  const span=Math.max(86_400_000,rows.length?Date.parse(`${rows.at(-1)!.day}T00:00:00Z`)-start:0);
  const x=(index:number)=>left+4+(Date.parse(`${rows[index].day}T00:00:00Z`)-start)/span*(right-left-8);
  const maximum=Math.max(1,Math.ceil(Math.max(0,...rows.flatMap(row=>components.map(key=>row.q3_by_component[key]??0)),...(showTotal?rows.map(row=>row.q3??0):[]))/10)*10);
  const indexY=(value:number)=>bottom-value/maximum*(bottom-top);
  const stressY=(value:number)=>bottom-value/100*(bottom-top);
  const ticks=rows.length?[...new Set([0,Math.floor((rows.length-1)/2),rows.length-1])]:[];
  const focusIndex=Math.max(0,Math.min(selectedIndex??rows.findLastIndex(row=>row.q3!==null||row.stress!==null),rows.length-1));
  const focus=rows[focusIndex];
  const decisionIndex=decision?[...rows].reverse().find(row=>row.q3_by_component[decision.component]!==null):undefined;
  const latestStress=[...rows].reverse().find(row=>row.stress!==null);
  const chooseDate=(clientX:number,node:SVGSVGElement)=> {
    const bounds=node.getBoundingClientRect();
    if(!bounds.width)return;
    const point=(clientX-bounds.left)/bounds.width*width;
    const index=rows.reduce((best,_row,index)=>Math.abs(x(index)-point)<Math.abs(x(best)-point)?index:best,0);
    setSelectedIndex(index);
  };
  return <figure className="learning-chart learning-linked-chart"><figcaption id={`${id}-title`}><strong>Тридневно натоварване и интегрален стрес</strong><span>Една времева ос · две отделни скали</span></figcaption>
    <div className="learning-zone-key" role="group" aria-label="Компоненти в графиката"><button type="button" aria-pressed={selection.length===LEARNING_COMPONENTS.length} onClick={()=>onSelection([...LEARNING_COMPONENTS])}>Всички</button>{LEARNING_COMPONENTS.map(key=><button key={key} type="button" aria-pressed={selection.includes(key)} onClick={()=>onSelection(selected===key?[...LEARNING_COMPONENTS]:[key])} style={{"--component-color":componentColor(key)} as CSSProperties}><i aria-hidden="true"/>{componentLabel(key)}</button>)}{accents.length>0&&<button type="button" aria-pressed={selection.join(",")===accents.join(",")} onClick={()=>onSelection(accents)}>Акценти</button>}<label className="learning-total-toggle"><input type="checkbox" checked={showTotal} onChange={event=>onTotal(event.target.checked)}/>Общо Q</label></div>
    <div className="learning-linked-key">{showTotal&&<span>Общо Q · тъмен пунктир</span>}<span><i className="learning-stress-swatch"/>Стрес · дясна ос</span><span><i className="learning-reference-swatch-line"/>Лична база на стреса</span></div>
    <div ref={holder}>{present?<svg role="img" aria-labelledby={`${id}-title ${id}-description`} viewBox={`0 0 ${width} ${height}`} width="100%" height={height} onPointerDown={event=>chooseDate(event.clientX,event.currentTarget)} onPointerMove={event=>{if(event.pointerType==="mouse"||event.buttons)chooseDate(event.clientX,event.currentTarget);}}>
      <desc id={`${id}-description`}>Едни и същи дати за тридневните средни на Q по компоненти по лявата скала и един претеглен стрес от 0 до 100 по дясната скала. {selected?`Показан е само компонент ${componentLabel(selected)}.`:"Цветовете обозначават шестте компонента."} Пунктирът по компонентите обозначава моделна оценка или непълно покритие. Общото Q се осреднява за дните с известни шест компонента. Прекъсването означава липсваща стойност.</desc>
      <text x={left} y={15} className="learning-axis-label">Q/ден · 3 дни</text><text x={right} y={15} textAnchor="end" className="learning-axis-label learning-stress-axis">Стрес · 0–100</text>
      {[0,.5,1].map(f=><g key={f}><line x1={left} y1={indexY(maximum*f)} x2={right} y2={indexY(maximum*f)} className="learning-grid"/><text x={left-9} y={indexY(maximum*f)+4} textAnchor="end">{learningNumber(maximum*f)}</text><text x={right+9} y={stressY(100*f)+4} className="learning-stress-axis">{learningNumber(100*f)}</text></g>)}
      {ticks.map(index=><text key={index} x={x(index)} y={bottom+27} textAnchor={index===0?"start":index===rows.length-1?"end":"middle"}>{rows[index].day.slice(8,10)}.{rows[index].day.slice(5,7)}</text>)}
      {components.map(key=><g key={key} data-component={key} style={{"--component-color":componentColor(key)} as CSSProperties}>{[false,true].flatMap(estimated=>learningQ3Paths(rows,key,estimated,x,indexY).map((d,index)=><path key={`${estimated}-${index}`} d={d} className={`learning-index-line${estimated?" is-estimated":""}`}/>))}{rows.map((row,index)=>row.q3_by_component[key]!==null&&(rows.length<=90||index===focusIndex)&&<circle key={row.day} cx={x(index)} cy={indexY(row.q3_by_component[key]!)} r={selected===key?2.5:1.4} className={`learning-index-mark${row.q3_partial?" is-estimated":""}`}><title>{`${learningDate(row.day)} · ${componentLabel(key)}: ${learningNumber(row.q3_by_component[key]!)} Q/ден${row.q3_partial?" · по наличните данни":""}`}</title></circle>)}</g>)}
      {showTotal&&learningPaths(rows.map(row=>({...row,baseline:row.q3,stress_basis:null,stress_reference:null,stress_quality:null,stress_weights:{}})),"baseline",x,indexY).map((d,index)=><path key={`total-${index}`} d={d} className="learning-reference" style={{stroke:"#334155",strokeDasharray:"5 3"}}><title>Общо Q · 3 дни</title></path>)}
      {learningPaths(rows,"baseline",x,stressY).map((d,index)=><path key={`base-${index}`} d={d} className="learning-reference"/>)}
      {[false,true].flatMap(partial=>learningPaths(rows.map(row=>({...row,stress:(row.stress_quality==="PARTIAL")===partial?row.stress:null})),"stress",x,stressY).map((d,index)=><path key={`stress-${partial}-${index}`} d={d} className={`learning-stress-line${partial?" is-partial":""}`}/>))}
      {rows.map((row,index)=>row.stress!==null&&(rows.length<=90||index===focusIndex)&&<circle key={row.day} cx={x(index)} cy={stressY(row.stress)} r="2" className="learning-stress-mark"><title>{`${learningDate(row.day)}: стрес ${learningNumber(row.stress)} точки${row.baseline!==null?`; лична база ${learningNumber(row.baseline)}`:""}`}</title></circle>)}
      {focus&&<line x1={x(focusIndex)} x2={x(focusIndex)} y1={top} y2={bottom} className="learning-date-cursor"/>}
    </svg>:<p className="learning-empty">Няма налични стойности за свързаната графика през този период.</p>}</div>
    {!hasIndex&&<p className="learning-caption">Няма годни измерени данни за тридневните средни на Q по компоненти през този период.</p>}
    {!hasStress&&<p className="learning-caption">Няма достатъчно съпоставими показатели за графика на стреса. <Link href="/response#daily-report">Попълни сутрешна оценка</Link></p>}
    {focus&&<div className="learning-chart-inspection"><label htmlFor={`${id}-date`}><strong>{learningDate(focus.day)}</strong><span>Избери ден</span></label><input id={`${id}-date`} type="range" aria-label="Дата в свързаната графика" min={0} max={Math.max(0,rows.length-1)} value={focusIndex} disabled={rows.length<2} onChange={event=>setSelectedIndex(Number(event.target.value))}/><div className="learning-day-values" aria-live="polite">{showTotal&&<span>Общо Q <strong>{focus.q3===null?"—":learningNumber(focus.q3)} Q/ден</strong></span>}<span className="learning-day-stress">Стрес <strong>{focus.stress===null?"—":learningNumber(focus.stress)}</strong></span>{components.map(key=><span key={key} style={{"--component-color":componentColor(key)} as CSSProperties}><i aria-hidden="true"/>{componentLabel(key)} <strong>{focus.q3_by_component[key]===null?"—":`${learningNumber(focus.q3_by_component[key]!)} Q/ден`}</strong></span>)}</div><p className={`learning-day-status${focus.load_status==="PARTIAL"||focus.load_status==="UNAVAILABLE"?" is-incomplete":""}`}>{loadStatusLabel[focus.load_status]}{focus.load_activity_count!==null?` · ${focus.load_activity_count} ${focus.load_activity_count===1?"активност":"активности"}`:""}.{focus.load_reason?` ${focus.load_reason}`:""}</p>
      {focus.stress_reason&&<p className="learning-day-status">{focus.stress_reason}{!!focus.stress_channels?.length&&` Показатели: ${focus.stress_channels.map(key=>CHANNEL_LABELS[key]??key).join(", ")}.`} {focus.stress!==null?"":<Link href="/response#daily-report">Към оценката на състоянието</Link>}</p>}
      <p className="learning-caption">Покритие за 3 календарни дни: {components.map(key=>`${componentLabel(key)} ${focus.q3_observed_days[key]??0}/3`).join(" · ")}.{focus.q3_partial?" Осредняването включва наличните записи; пунктирът обозначава непълни или оценени данни.":""}</p>
      {components.some(key=>focus.recorded_q_by_component[key]!==null)&&<p className="learning-day-recorded">Отчетено Q: {components.filter(key=>focus.recorded_q_by_component[key]!==null).map(key=>`${componentLabel(key)} ${learningNumber(focus.recorded_q_by_component[key]!)}`).join(" · ")}{focus.load_unknown_minutes!==null&&focus.load_unknown_minutes>0?` · ${learningNumber(focus.load_unknown_minutes)} мин без разпределение по компоненти`:""}.</p>}
    </div>}
    <p className="learning-caption">Цветните линии показват средното Q за текущия и предходните два завършени календарни дни.{showTotal?" Тъмният пунктир е общото Q.":""} Почивката участва с нула. Липсваща стойност не е нулево натоварване; средната е по наличните дни и покритието е посочено. Стресът има собствена скала вдясно; пунктирът при него означава частична оценка. Промяната на набора показатели прекъсва линията.</p>
    {decision?<div className="learning-chart-decision"><span>Следваща промяна · {componentLabel(decision.component)}</span><strong>{decision.proposed_change_percent===null?"—":learningChange(decision.proposed_change_percent)}</strong><p className="learning-decision-context">Последни налични стойности: {componentLabel(decision.component)} Q/ден (3 дни) {decisionIndex?`${learningNumber(decisionIndex.q3_by_component[decision.component]! )} (${learningDate(decisionIndex.day)})${decisionIndex.q3_partial?" · по наличните данни":""}`:"—"} · стрес {latestStress?`${learningNumber(latestStress.stress!)} (${learningDate(latestStress.day)})`:"—"}.</p><p>{basisLabel[decision.management_basis]}. {decision.reason}</p><a href={`#component-${decision.component}`}>Оценка и основание за {componentLabel(decision.component)} →</a></div>:<div className="learning-chart-decision"><span>Няма избрана следваща промяна</span><p>{decisionReason}</p></div>}
  </figure>;
}
const validationLabel:Record<string,string> = { WARMUP:"Натрупване на наблюдения",PASSED:"Проверени прогнози",FAILED:"Нужна е допълнителна проверка" };
const responseLabel:Record<ComponentLearning["response_status"],string> = { SUPPORTED_POSITIVE:"Положителна връзка",SUPPORTED_NEGATIVE:"Отрицателна връзка",UNCERTAIN:"Неопределена връзка",INSUFFICIENT_DATA:"Събира данни" };
const actionLabel:Record<ComponentLearning["action_status"],string> = { COLLECTING:"Нужни са още наблюдения", SELECTED:"Избрана следваща промяна", WAITING:"Изчаква текущото проследяване", NOT_ELIGIBLE:"Не е избран за текущия период" };
const basisLabel:Record<ComponentLearning["management_basis"],string> = { POLICY_REDUCTION:"Правило за намаляване",EXPLORATION:"Проучване",MODEL_SUPPORTED:"Подкрепено от модела",HOLD:"Запазване на текущата доза" };
const quantity = (value:number|null) => value===null?"—":learningNumber(value);

function ComponentAssessment({ item, indexDay }: { item: ComponentLearning; indexDay: LearningDay | undefined }) {
  const selected = item.action_status==="SELECTED";
  return <article className={`learning-component${selected?" is-selected":""}`} style={{"--component-color":componentColor(item.component)} as CSSProperties} aria-labelledby={`component-${item.component}`}>
    <header><h3 id={`component-${item.component}`}><i aria-hidden="true"/>{componentLabel(item.component)}</h3><span className="learning-response-label">{responseLabel[item.response_status]}</span></header>
    <dl className="learning-component-load"><div><dt>7 дни · Q/седм.</dt><dd>{quantity(item.recent_weekly_q)}</dd></div><div><dt>40 дни · Q/седм.</dt><dd>{quantity(item.reference_weekly_q)}</dd></div><div><dt>3 дни · Q/ден</dt><dd>{quantity(item.q3)}<small>{item.q3_observed_days}/3 дни</small></dd></div></dl>
    {item.predicted_stress_points!==null&&<p className="learning-model-response">При +5% Q: <strong>{item.predicted_stress_points>0?"+":""}{learningNumber(item.predicted_stress_points)} т. стрес</strong> · предварителна оценка.</p>}
    <p className="learning-model-response">{item.predicted_performance_percent===null?"Работоспособността още няма съпоставим резултат. Натоварването и стресът се проследяват.":<>Сравнение при +{item.test_step_percent}% Q: <strong>{learningChange(item.predicted_performance_percent)}</strong> {item.model_scope==="GLOBAL"?"обща работоспособност":"работоспособност"}.</>}</p>
    {selected?<div className="learning-component-decision" aria-label={`Следваща промяна ${componentLabel(item.component)}`}><span>{basisLabel[item.management_basis]}</span><dl><div><dt>Предложена промяна</dt><dd>{item.proposed_change_percent===null?"—":learningChange(item.proposed_change_percent)}</dd></div><div><dt>Допуснато от модела</dt><dd>{item.applied_change_percent===null?"—":learningChange(item.applied_change_percent)}</dd></div></dl></div>:<p className="learning-action-label">{actionLabel[item.action_status]}</p>}
    <details className="learning-component-details"><summary>Основание за {componentLabel(item.component)}</summary>{indexDay&&<p>Индекс 7/40 към {learningDate(indexDay.day)}: {learningNumber(indexDay.index_7_40_by_component[item.component]!)}×{indexDay.index_load_status!=="COMPLETE"?" · по наличните данни":""}.</p>}<p>{item.reason}</p><p>Акцент Q7/Q40: {item.accent_ratio===null?"—":`${learningNumber(item.accent_ratio)}×`}. Това е отношението на средното реално Q за 7 и 40 дни; то е различно от индекса 7/40.</p>{item.predicted_stress_points!==null&&<p>Стрес: {item.stress_evidence_count} наблюдения · интервал {learningNumber(item.predicted_stress_low!)} до {learningNumber(item.predicted_stress_high!)} т. Едновременните промени по зони ограничават отделянето на ефектите.</p>}<p>{item.evidence_count} съпоставими резултата · {item.isolated_changes} отделими промени · {validationLabel[item.validation_status]??"Оценката се проверява"}.</p><p>Покритие на реалното Q: {item.recent_observed_days}/7 и {item.reference_observed_days}/40 дни. Средните са по наличните дни; „—“ означава липса на наблюдения.</p>
      {selected&&item.baseline_actual_weekly_q!==null&&<p>База на съпоставимия блок: {learningNumber(item.baseline_actual_weekly_q)} Q/седм.{item.target_weekly_q!==null?` → цел ${learningNumber(item.target_weekly_q)} Q/седм.`:""}{item.execution_weekly_q_ceiling!==null?` · текущ таван ${learningNumber(item.execution_weekly_q_ceiling)} Q/седм.`:""} Това е отделна база от текущото 7/40.</p>}
      {selected&&item.effective_from&&<p>Следващ период: {learningDate(item.effective_from)}{item.expires_on?` – ${learningDate(item.expires_on)}`:""}.</p>}
      {item.predicted_performance_percent!==null&&<><p>Моделен интервал: {learningChange(item.predicted_performance_low!)} до {learningChange(item.predicted_performance_high!)}. Обхват: {item.model_scope==="GLOBAL"?"обща работоспособност":componentLabel(item.model_scope??item.component)}.</p><p>Стандартното сравнение при +5% Q описва предварителна или проверена връзка според броя наблюдения и проверката на прогнозите с резултата, а не доказана причинност или отделна препоръка.</p></>}
    </details>
  </article>;
}

function recoveryObservationLabel(status: string) {
  const labels: Record<string,string> = { NO_ELEVATED_RESPONSE:"Няма отчетено повишение на стреса", INSUFFICIENT_DATA:"Недостатъчни наблюдения", INSUFFICIENT_FOLLOWUP:"Липсват съпоставими оценки за връщането", CONTEXT_REVIEW:"Контекст за отделен преглед", DELAYED:"Наблюдавана повишена реакция след срока", OBSERVING:"Проследяването продължава" };
  return labels[status]??"Няма оценка на връщането";
}

export function RecoveryLearning({ view, period }: { view: RecoveryLearningView; period?:{days:number;end:string;components?:string;total:boolean} }) {
  const requested=(period?.components??"").split(",").filter((key):key is LearningComponent=>LEARNING_COMPONENTS.includes(key as LearningComponent));
  const initial=period?.components==="all"?[...LEARNING_COMPONENTS]:requested.length?[...new Set(requested)]:period&&view.active_components.length?view.active_components:[...LEARNING_COMPONENTS];
  const [selection,setSelection]=useState<LearningComponent[]>(initial);
  const [showTotal,setShowTotal]=useState(period?.total??false);
  const [showNumbers,setShowNumbers]=useState(false);
  const episodes = [...view.episodes].sort((a,b)=>b.start.localeCompare(a.start));
  const recent = episodes.slice(0,5);
  const recoveryMax = Math.max(1,...recent.flatMap(item=>[item.recovery_limit_days,item.recovery_days??0]));
  const latestStress = [...view.series].reverse().find(row=>row.stress!==null);
  const last = latestStress ?? view.series.at(-1);
  const panelLabel = last?.stress_basis==="FROZEN_BLOCK"?"Един и същ набор за проследения блок":last?.stress_basis==="AVAILABLE_PANEL"?"Оценка по наличните показатели за деня":last?.stress_basis==="FIXED_PANEL"?"Постоянен набор за периода":"Няма достатъчно съпоставими показатели";
  return <>{period&&<form className="learning-period" method="get" aria-label="Период на графиката">
    <input type="hidden" name="components" value={selection.length===6?"all":selection.join(",")}/>
    <input type="hidden" name="total" value={showTotal?"1":"0"}/>
    <div className="learning-period-presets" role="group" aria-label="Бърз избор на период">{[[14,"14 дни"],[30,"1 месец"],[60,"2 месеца"],[90,"3 месеца"],[366,"1 година"]].map(([value,label])=><button key={value} type="submit" name="days" value={value} aria-pressed={period.days===value}>{label}</button>)}</div>
    <label>До дата<input type="date" name="end" defaultValue={period.end} max={view.as_of}/></label><button className="action-button secondary" type="submit" name="days" value={period.days}>Покажи</button>
    {view.history&&<p className="learning-caption">Показан период: {learningDate(view.history.display_start)} – {learningDate(view.history.display_end)}.{view.history.limited?` Наличната история е ${view.history.available_start?learningDate(view.history.available_start):"неподготвена"}${view.history.available_end?` – ${learningDate(view.history.available_end)}`:""}; липсващите дни не са нулево натоварване.`:""} Текущата оценка и предложението са към {learningDate(view.as_of)}.{view.history.preparing?" Историческите оценки се обновяват във фонов режим.":""}</p>}
  </form>}<div className="learning-content">
    <section className="learning-panel learning-current" aria-labelledby="learning-current-heading"><div className="learning-heading"><h2 id="learning-current-heading">{LEARNING_MODE_LABELS[view.mode]}</h2><span>{view.evidence_count} съпоставими {view.evidence_count===1?"наблюдение":"наблюдения"}</span></div><p className="learning-summary">{view.summary}</p>
      {view.mode==="SHADOW"&&<p className="learning-caption">Предложенията се проследяват в „Наблюдение“ и още не променят тренировъчната доза.</p>}
      {view.mode==="CONTROL"&&<p className="learning-caption">Генераторът може да намали допуснатата корекция според периода, готовността и метода. Фиксираната програма изисква одобрение.</p>}
      {view.evidence_count===0&&<Link className="learning-observation-link" href="/response">Към наблюденията за стрес и възстановяване →</Link>}
    </section>
    <section className="learning-panel" aria-label="Натоварване и независима реакция"><div className="learning-heading"><h2>Натоварване и реакция</h2><span>{view.series.length?`${learningDate(view.series[0].day)} – ${learningDate(view.series.at(-1)!.day)}`:"Няма наблюдаван период"}</span></div><div className="learning-charts"><Timeline rows={view.series} decision={view.components.find(item=>item.action_status==="SELECTED")} decisionReason={view.decision.reason} selection={selection} onSelection={setSelection} showTotal={showTotal} onTotal={setShowTotal} accents={view.active_components}/></div><p className="learning-caption">Стресът съчетава независимите показатели за състоянието с тегла. Той не се изчислява от тренировъчното Q.</p></section>
    <section className="learning-panel" aria-labelledby="learning-components-heading"><div className="learning-heading"><h2 id="learning-components-heading">Как реагираш на всеки компонент</h2><span>Една следваща промяна за проследяване</span></div><p className="learning-caption">Тридневната средна показва текущата доза. Оценката включва наличните записи, а покритието и проверката на прогнозите остават видими.</p><div className="learning-component-grid">{view.components.map(item=><ComponentAssessment key={item.component} item={item} indexDay={[...view.series].reverse().find(row=>row.index_7_40_by_component[item.component]!==null)}/>)}</div>
      {Number(view.observations.reaction_episodes)>0&&view.observations.performance_observations===0&&<p className="learning-caption">Наблюдаваната реакция се отчита. Още няма съпоставим резултат за оценка на тренировъчния ефект.</p>}
    </section>
    <details className="learning-panel learning-details"><summary>Наблюдения и основания</summary>
      <h3>Показатели на стреса</h3><p>{latestStress?`Последна оценка на стреса · ${learningDate(latestStress.day)}. `:""}{panelLabel}{!latestStress&&last?` · ${learningDate(last.day)}`:""}. {last?.stress_quality==="PARTIAL"?"Оценката е частична.":last?.stress_reason}</p><p className="learning-caption">Теглата показват относителната важност и се нормализират за наличния набор. Те не са процент увереност. Връщането след блок се проверява по едни и същи показатели; пропускът не доказва забавено възстановяване.</p>
      <div className="learning-table-wrap"><table><caption>Показатели и тегла{last?` за ${learningDate(last.day)}`:""}</caption><thead><tr><th>Показател</th><th>Номинално тегло</th><th>Ефективно тегло</th><th>Дни с данни</th></tr></thead><tbody>{view.channels.map(item=><tr key={item.key}><th scope="row">{item.label}</th><td>{item.nominal_weight===null?"—":`${learningNumber(item.nominal_weight)} т.`}</td><td>{last?.stress_weights[item.key]===undefined?"—":`${learningNumber(last.stress_weights[item.key])}%`}</td><td>{item.observed_days}</td></tr>)}</tbody></table></div>
      <h3>Време за възстановяване</h3>{recent.length?<><div className="learning-recovery-key"><span><i className="learning-reference-swatch"/>Експертен срок</span><span><i className="learning-observed-swatch"/>Наблюдавано връщане</span><span>Дни</span></div><div className="learning-recovery-bars">{recent.map(item=><div className="learning-recovery-row" key={item.id}><span>{learningDate(item.start)}<small>{item.loading_days} дни натоварване</small></span><div><div className="learning-recovery-track"><span className="learning-reference-bar" style={{width:`${100*item.recovery_limit_days/recoveryMax}%`}}/><span>{learningNumber(item.recovery_limit_days)} дни</span></div><div className="learning-recovery-track">{item.recovery_days!==null?<><span className="learning-observed-bar" style={{width:`${100*item.recovery_days/recoveryMax}%`}}/><span>{learningNumber(item.recovery_days)} дни</span></>:<span className="learning-caption">{recoveryObservationLabel(item.status)}</span>}</div></div></div>)}</div></>:<p className="learning-empty">Няма завършен съпоставим блок с достатъчно данни за възстановяването.</p>}
      {episodes.length>0&&<div className="learning-table-wrap"><table><caption>Проследени блокове</caption><thead><tr><th>Блок</th><th>Връщане / срок</th><th>Работоспособност</th><th>Основание</th></tr></thead><tbody>{episodes.map(item=><tr key={item.id}><td>{learningDate(item.start)} – {learningDate(item.load_end)}</td><td>{item.recovery_days===null?recoveryObservationLabel(item.status):`${learningNumber(item.recovery_days)} дни`} / {learningNumber(item.recovery_limit_days)} дни</td><td>{item.performance_change_percent===null?"Няма съпоставим резултат":learningChange(item.performance_change_percent)}</td><td>{item.reason}</td></tr>)}</tbody></table></div>}
      <details onToggle={event=>setShowNumbers(event.currentTarget.open)}><summary>Числови данни от графиките</summary>{showNumbers&&<div className="learning-table-wrap"><table><thead><tr><th>Дата</th>{LEARNING_COMPONENTS.map(key=><th key={key}>{componentLabel(key)} · отчетено Q</th>)}{LEARNING_COMPONENTS.map(key=><th key={`q3-${key}`}>{componentLabel(key)} · Q/ден (3 дни)</th>)}{LEARNING_COMPONENTS.map(key=><th key={`index-${key}`}>{componentLabel(key)} · 7/40</th>)}<th>Покритие</th><th>Общо Q за самообучение</th><th>Стрес</th><th>Лична база</th></tr></thead><tbody>{view.series.map(row=><tr key={row.day}><td>{learningDate(row.day)}</td>{LEARNING_COMPONENTS.map(key=><td key={key}>{quantity(row.recorded_q_by_component[key]??row.actual_q_by_component[key])}</td>)}{LEARNING_COMPONENTS.map(key=><td key={`q3-${key}`}>{quantity(row.q3_by_component[key])}</td>)}{LEARNING_COMPONENTS.map(key=><td key={`index-${key}`}>{quantity(row.index_7_40_by_component[key])}</td>)}<td>{loadStatusLabel[row.load_status]}{row.load_reason?` · ${row.load_reason}`:""}</td><td>{quantity(row.actual_q)}</td><td>{quantity(row.stress)}</td><td>{quantity(row.baseline)}</td></tr>)}</tbody></table></div>}</details>
    </details>
    <details className="learning-panel learning-details"><summary>Как се взема решението</summary><p>Моделът осреднява изпълненото Q за три календарни дни и го съпоставя с независимите наблюдения за стрес. Непълните записи участват с отбелязано покритие; не се изискват пълни седмици или минимум осем резултата за показване на оценката. Съпоставимите тестове оценяват работоспособността отделно. При 7, 14 и 21 дни натоварване експертният срок е съответно 3, 5 и 7 дни; междинните стойности се изчисляват плавно.</p><p>Само един компонент може да има избрана следваща промяна с до 20% спрямо реалната база на съпоставим блок. Сравненията при +5% Q са оценки на модела, а не шест едновременни препоръки. Разтоварването се определя отделно; обновяването не натрупва промени.</p><p>Сроковете са експертни настройки за управление и не определят медицинска диагноза. Бързото възстановяване само по себе си не доказва тренировъчно подобрение.</p>{view.limitations.length>0&&<ul>{view.limitations.map((item,index)=><li key={index}>{item}</li>)}</ul>}<Link href="/planning">Настройки на планирането →</Link></details>
  </div></>;
}
