"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";
import { LEARNING_MODE_LABELS } from "../lib/individual-learning";
import { learningChange, learningDate, learningNumber, type LearningDay, type RecoveryLearningView } from "../lib/recovery-learning";

/** Nulls break paths, including absent dates. Unobserved days cannot imply recovery. */
export function learningPaths(rows: LearningDay[], value: "stress" | "baseline", x: (index: number) => number, y: (value: number) => number) {
  const paths: string[] = [];
  let path = "";
  rows.forEach((row,index) => {
    const previous = rows[index-1];
    const gap = previous && Date.parse(`${row.day}T00:00:00Z`)-Date.parse(`${previous.day}T00:00:00Z`) > 86_400_000;
    if (row[value] === null || gap) { if (path) paths.push(path); path = ""; }
    if (row[value] !== null) path += `${path ? "L" : "M"}${x(index)},${y(row[value])} `;
  });
  if (path) paths.push(path);
  return paths;
}

function Timeline({ rows, kind }: { rows: LearningDay[]; kind: "load" | "stress" }) {
  const holder = useRef<HTMLDivElement>(null);
  const [width,setWidth] = useState(600);
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
  const title = kind === "load" ? "Реално изпълнено натоварване" : "Наблюдавана реакция";
  const present = rows.filter(row => kind === "load" ? row.actual_q !== null : row.stress !== null);
  const left=48, right=width-14, top=24, bottom=174, height=216;
  const start = rows[0] ? Date.parse(`${rows[0].day}T00:00:00Z`) : 0;
  const span = Math.max(86_400_000, rows.length ? Date.parse(`${rows.at(-1)!.day}T00:00:00Z`)-start : 0);
  const x = (index:number) => left+5+(Date.parse(`${rows[index].day}T00:00:00Z`)-start)/span*(right-left-10);
  const maximum = kind === "load" ? Math.max(1,...rows.map(row => row.actual_q ?? 0)) : 100;
  const y = (value:number) => bottom-value/maximum*(bottom-top);
  const ticks = rows.length ? [...new Set([0,Math.floor((rows.length-1)/2),rows.length-1])] : [];
  const barWidth = Math.max(1,Math.min(12,(right-left)/Math.max(1,span/86_400_000+1)*.7));
  return <figure className="learning-chart"><figcaption id={`${id}-title`}><strong>{title}</strong><span>{kind === "load" ? "Q · еквивалентни минути за деня" : "Стрес · точки от 0 до 100"}</span></figcaption>
    <div ref={holder}>{present.length ? <svg role="img" aria-labelledby={`${id}-title ${id}-description`} viewBox={`0 0 ${width} ${height}`} width="100%" height={height}>
      <desc id={`${id}-description`}>{kind === "load" ? "Дневно натоварване от изпълнените тренировки." : "Стрес от независими наблюдения; прекъсванията означават липсващи данни. Пунктирът е личната референтна стойност."}</desc>
      {[0,.5,1].map(f => <g key={f}><line x1={left} y1={y(maximum*f)} x2={right} y2={y(maximum*f)} className="learning-grid"/><text x={left-9} y={y(maximum*f)+4} textAnchor="end">{learningNumber(maximum*f)}</text></g>)}
      {ticks.map(index => <text key={index} x={x(index)} y={bottom+25} textAnchor={index===0?"start":index===rows.length-1?"end":"middle"}>{rows[index].day.slice(8,10)}.{rows[index].day.slice(5,7)}</text>)}
      {kind === "load" ? rows.map((row,index) => row.actual_q !== null && <g key={row.day}><rect x={x(index)-barWidth/2} y={y(row.actual_q)} width={barWidth} height={Math.max(0,bottom-y(row.actual_q))} className="learning-load-mark"/>{row.actual_q===0&&<circle cx={x(index)} cy={bottom} r="2" className="learning-load-mark"/>}<title>{`${learningDate(row.day)}: ${learningNumber(row.actual_q)} Q`}</title></g>) : <>
        {learningPaths(rows,"baseline",x,y).map((d,index)=><path key={`base-${index}`} d={d} className="learning-reference"/>)}
        {learningPaths(rows,"stress",x,y).map((d,index)=><path key={`stress-${index}`} d={d} className="learning-stress-line"/>)}
        {rows.map((row,index)=>row.stress!==null&&<circle key={row.day} cx={x(index)} cy={y(row.stress)} r="2.5" className="learning-stress-mark"><title>{`${learningDate(row.day)}: ${learningNumber(row.stress)} точки${row.baseline!==null?`; лична база ${learningNumber(row.baseline)}`:""}`}</title></circle>)}
      </>}
    </svg> : <p className="learning-empty">{kind==="load"?"Няма годни измерени данни за натоварването през този период.":"Няма достатъчно независими наблюдения за графика на стреса."}</p>}</div>
    {kind === "stress" && present.length > 0 && <p className="learning-caption">Плътна линия: наблюдаван стрес · пунктир: лична база · прекъсване: липсваща оценка.</p>}
  </figure>;
}

const validationLabel:Record<string,string> = { WARMUP:"Натрупване на наблюдения",PASSED:"Проверени прогнози",FAILED:"Нужна е допълнителна проверка" };

export function RecoveryLearning({ view }: { view: RecoveryLearningView }) {
  const { decision } = view;
  const episodes = [...view.episodes].sort((a,b)=>b.start.localeCompare(a.start));
  const recent = episodes.slice(0,5);
  const recoveryMax = Math.max(1,...recent.flatMap(item=>[item.recovery_limit_days,item.recovery_days??0]));
  return <div className="learning-content">
    <section className="learning-panel learning-current" aria-labelledby="learning-current-heading">
      <div className="learning-heading"><h2 id="learning-current-heading">{LEARNING_MODE_LABELS[view.mode]}</h2><span>{view.evidence_count} съпоставими {view.evidence_count===1?"наблюдение":"наблюдения"}</span></div>
      <p className="learning-summary">{view.summary}</p>
      {Number(view.observations.reaction_episodes)>0 && view.observations.performance_observations===0 && <p className="learning-caption">Наблюдаваната реакция се отчита. Още няма съпоставим резултат за оценка на тренировъчния ефект.</p>}
      <div className="learning-decision"><div><span>Предложена промяна{decision.component ? ` · ${decision.component==="STR"?"сила":decision.component}` : ""}</span><strong>{learningChange(decision.proposed_change_percent)}</strong></div><div><span>Допуснато от модела</span><strong>{learningChange(decision.applied_change_percent)}</strong></div></div>
      <p>{decision.reason}</p>
      {view.mode === "SHADOW" && <p className="learning-caption">Предложенията се проследяват в „Наблюдение“. Те още не променят тренировъчната доза.</p>}
      {view.mode === "CONTROL" && <p className="learning-caption">Генераторът може да намали допуснатата корекция според периода, готовността и метода. Фиксираната програма изисква одобрение.</p>}
      {view.evidence_count===0&&<Link className="learning-observation-link" href="/response">Към наблюденията за стрес и възстановяване →</Link>}
    </section>
    <section className="learning-panel" aria-label="Натоварване и независима реакция"><div className="learning-heading"><h2>Натоварване и реакция</h2><span>{view.series.length?`${learningDate(view.series[0].day)} – ${learningDate(view.series.at(-1)!.day)}`:"Няма наблюдаван период"}</span></div>
      <div className="learning-charts"><Timeline rows={view.series} kind="load"/><Timeline rows={view.series} kind="stress"/></div>
      <p className="learning-caption">Натоварването и стресът са различни величини. Стресът идва от наблюденията за състоянието.</p>
    </section>
    <section className="learning-panel" aria-labelledby="learning-recovery-heading"><h2 id="learning-recovery-heading">Време за възстановяване</h2>
      {recent.length ? <><div className="learning-recovery-key"><span><i className="learning-reference-swatch"/>Експертен срок</span><span><i className="learning-observed-swatch"/>Наблюдавано връщане</span><span>Дни</span></div>
        <div className="learning-recovery-bars">{recent.map(item=><div className="learning-recovery-row" key={item.id}><span>{learningDate(item.start)}<small>{item.loading_days} дни натоварване</small></span><div><div className="learning-recovery-track"><span className="learning-reference-bar" style={{width:`${100*item.recovery_limit_days/recoveryMax}%`}}/><span>{learningNumber(item.recovery_limit_days)} дни</span></div><div className="learning-recovery-track">{item.recovery_days!==null?<><span className="learning-observed-bar" style={{width:`${100*item.recovery_days/recoveryMax}%`}}/><span>{learningNumber(item.recovery_days)} дни</span></>:<span className="learning-caption">{item.status==="INSUFFICIENT_DATA"?"Недостатъчни наблюдения":"Връщането още не е потвърдено"}</span>}</div></div></div>)}</div>
      </> : <p className="learning-empty">Няма завършен съпоставим блок с достатъчно данни за възстановяването.</p>}
    </section>
    <details className="learning-panel learning-details"><summary>Наблюдения и основания</summary>
      <p>{validationLabel[view.validation.status]??"Оценка на прогнозите"} · {view.validation.evaluated} проверки върху последващи данни.</p>
      <ul className="learning-channels">{view.channels.map(item=><li key={item.key}><span>{item.label}</span><span>{item.observed_days} дни с данни</span></li>)}</ul>
      {episodes.length>0&&<div className="learning-table-wrap"><table><caption>Проследени блокове</caption><thead><tr><th>Блок</th><th>Връщане / срок</th><th>Работоспособност</th><th>Основание</th></tr></thead><tbody>{episodes.map(item=><tr key={item.id}><td>{learningDate(item.start)} – {learningDate(item.load_end)}</td><td>{item.recovery_days===null?"Непотвърдено":`${learningNumber(item.recovery_days)} дни`} / {learningNumber(item.recovery_limit_days)} дни</td><td>{item.performance_change_percent===null?"Няма съпоставим резултат":learningChange(item.performance_change_percent)}</td><td>{item.reason}</td></tr>)}</tbody></table></div>}
      <details><summary>Числови данни от графиките</summary><div className="learning-table-wrap"><table><thead><tr><th>Дата</th><th>Реално Q</th><th>Наблюдаван стрес</th><th>Лична база</th></tr></thead><tbody>{view.series.map(row=><tr key={row.day}><td>{learningDate(row.day)}</td><td>{row.actual_q===null?"Няма данни":learningNumber(row.actual_q)}</td><td>{row.stress===null?"Няма данни":learningNumber(row.stress)}</td><td>{row.baseline===null?"Няма данни":learningNumber(row.baseline)}</td></tr>)}</tbody></table></div></details>
    </details>
    <details className="learning-panel learning-details"><summary>Как се взема решението</summary><p>Моделът използва изпълненото натоварване, независимите наблюдения и съпоставими резултати. При 7, 14 и 21 дни натоварване експертният срок е съответно 3, 5 и 7 дни; междинните стойности се изчисляват плавно.</p><p>Предложението може да увеличи, запази или намали компонентното Q с до 20% спрямо реалната база на следващ съпоставим блок. Разтоварването се определя отделно. Една промяна се проследява без повторно натрупване при обновяване.</p><p>Сроковете са експертни настройки за управление и не определят медицинска диагноза. Бързото възстановяване само по себе си не доказва тренировъчно подобрение.</p>{view.limitations.length>0&&<ul>{view.limitations.map((item,index)=><li key={index}>{item}</li>)}</ul>}<Link href="/planning">Настройки на планирането →</Link></details>
  </div>;
}
