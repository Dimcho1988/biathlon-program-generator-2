"use client";

import Link from "next/link";
import { PlanComparison } from "./plan-comparison";
import { LoadProgressionSummary } from "./load-progression-summary";
import { useState } from "react";
import { isRecord } from "../lib/training-status";
import { COMPONENTS, PHASE_LABELS, type Component, type PlanProjection, type PlanOutcome } from "../lib/training-management";

const COLORS: Record<Component, string> = { Z1: "#317997", Z2: "#33936b", Z3: "#b48211", Z4: "#d46b36", Z5: "#be4968", STR: "#7966ba" };
const EVENT: Record<string, string> = { MAIN_RACE: "Основен старт", CONTROL_RACE: "Контролен старт", CAMP: "Лагер", TEST: "Тест", UNAVAILABLE: "Без тренировки" };
const label = (value: unknown) => String(value ?? "");
const shortDate = (day: string) => day.slice(8, 10) + "." + day.slice(5, 7);
const numeric = (value: unknown): number | null => typeof value === "number" && Number.isFinite(value) ? value : null;
const display = (value: number | null) => value === null ? "—" : value.toLocaleString("bg-BG", { maximumFractionDigits: 2 });
interface Week { volume_budget_minutes: number | null; cycle?: Record<string, unknown>; start_date: string; end_date: string; accents: string[]; phases: string[]; components: Partial<Record<Component, { target_weekly_effective: number | null; target_index_7_40: number | null }>> }
function outlookWeeks(plan?: PlanProjection): Week[] {
  const outlook = plan?.long_term;
  if (!isRecord(outlook) || outlook.schema_version !== "training-outlook-v1" || !Array.isArray(outlook.weeks)) return [];
  return outlook.weeks.filter(isRecord).map(w => ({ volume_budget_minutes: numeric(w.volume_budget_minutes), cycle: isRecord(w.cycle) ? w.cycle : undefined, start_date: label(w.start_date), end_date: label(w.end_date), accents: Array.isArray(w.accents) ? w.accents.map(label) : [], phases: Array.isArray(w.phases) ? w.phases.map(label) : [], components: Object.fromEntries(COMPONENTS.map(z => {
    const c = isRecord(w.components) && isRecord(w.components[z]) ? w.components[z] : {};
    return [z, { target_weekly_effective: numeric(c.target_weekly_effective), target_index_7_40: numeric(c.target_index_7_40) }];
  })) }));
}

export function TrainingPlanOverview({ plan, outcomes, today, stale, currentProfileRevision, volumeContext }: { plan?: PlanProjection; outcomes: PlanOutcome[]; today: string; stale?: boolean; currentProfileRevision?: number; volumeContext?: Record<string, unknown> }) {
  const [metric, setMetric] = useState<"target_weekly_effective" | "target_index_7_40">("target_weekly_effective");
  const [zones, setZones] = useState<Component[]>([...COMPONENTS]);
  const [selected, setSelected] = useState(0);
  const weeks = outlookWeeks(plan);
  const current = weeks[Math.min(selected, weeks.length - 1)];
  const periodization = isRecord(plan?.periodization) ? plan.periodization : {};
  const phases = Array.isArray(periodization.phases) ? periodization.phases.filter(isRecord) : [];
  const snapshot = isRecord(plan?.input_snapshot) ? plan.input_snapshot : {};
  const calendar = isRecord(snapshot.calendar) ? snapshot.calendar : {};
  const events = Array.isArray(calendar.events) ? calendar.events.filter(isRecord) : [];
  const shownEvents = current ? events.filter(e => label(e.start_date) <= current.end_date && label(e.end_date) >= current.start_date) : [];
  const maximum = Math.max(metric === "target_index_7_40" ? 1.2 : 1, ...weeks.flatMap(w => zones.map(z => w.components[z]?.[metric] ?? 0))) * 1.1;
  const x = (index: number) => 60 + index * 770 / Math.max(1, weeks.length - 1);
  const y = (value: number) => 260 - value * 220 / maximum;
  return <section className="management-overview" aria-label="Дългосрочна подготовка">
    <LoadProgressionSummary plan={plan}/>
    {volumeContext && <section className="management-panel"><h2>Обем на подготовката</h2>
      <p className="management-muted">Актуални цели от записания профил · версия {currentProfileRevision}. Промените в профила се отразяват тук при отваряне; седмичните тренировки се подготвят отделно.</p>
      <div className="management-metrics"><div><small>Историческа основа за програмата</small><strong>{display(numeric(volumeContext.historical_training_weekly_minutes) === null ? null : Number(volumeContext.historical_training_weekly_minutes)/60)} ч</strong><span>средно за 7 дни</span></div><div><small>Налично време</small><strong>{volumeContext.availability_mode==="AUTO_HISTORY"?"Автоматично":`${display(numeric(volumeContext.available_weekly_minutes)===null?null:Number(volumeContext.available_weekly_minutes)/60)} ч`}</strong><span>{volumeContext.availability_mode==="AUTO_HISTORY"?"от историята и 7/40":"за 7 дни"}</span></div><div><small>Ориентировъчен обем за седмицата</small><strong>{display(current?.volume_budget_minutes == null ? null : current.volume_budget_minutes/60)} ч</strong><span>еквивалент при досегашната структура</span></div></div>
      {typeof volumeContext.available_weekly_minutes === "number" && Number(volumeContext.available_weekly_minutes) < Number(volumeContext.historical_training_weekly_minutes) && <p className="management-notice">Записаното свободно време е по-малко от историческия обем и ограничава програмата. <Link href="/planning">Провери дните и минутите в профила →</Link></p>}
      <p className="management-muted">Историята е началната база; 7/40 задава целевия товар по зони. Часовете са ориентир при досегашното съотношение между време и товар, а не лимит. Точният обем зависи от методите и дневната готовност. При изключена сила нейното време се отделя.</p>
    </section>}
    <section className="management-panel"><h2>Посока на подготовката</h2>
      {!plan ? <p>Запази профил за планиране, за да видиш разпределението според целите и календара.</p> : <>
        {stale && <p className="management-notice">Показана е запазената версия. Преизчисли програмата от седмичния изглед, за да включиш последните данни.</p>}
        <div className="management-phase-list">{phases.map((p, i) => <div key={i} className={label(p.start_date) <= today && label(p.end_date) >= today ? "is-current" : ""}><span>{shortDate(label(p.start_date))} – {shortDate(label(p.end_date))}</span><strong>{PHASE_LABELS[label(p.kind)] ?? label(p.kind)}</strong><small>{label(p.days)} дни</small></div>)}</div>
      </>}
    </section>
    <section className="management-panel"><h2>Динамика по зони</h2><p className="management-muted">Целеви товар според периода, акцентите и разтоварването. Дневната доза се уточнява отделно според готовността.</p>
      {isRecord(plan?.long_term) && plan.long_term.limited === true && <p className="management-notice">Данните за дневната готовност са непълни или неактуални. Показани са зададените цели; допустимите тренировки се проверяват отделно след обновяване на данните.</p>}
      {weeks.length === 0 ? <p>Няма бъдещи седмици за показване. Провери периода на подготовката в профила.</p> : <>
        <div className="management-view-switch" role="group" aria-label="Мярка на динамиката"><button type="button" aria-pressed={metric === "target_weekly_effective"} onClick={() => setMetric("target_weekly_effective")}>Товар по зони</button><button type="button" aria-pressed={metric === "target_index_7_40"} onClick={() => setMetric("target_index_7_40")}>Целеви 7/40</button></div>
        <div className="management-zone-legend" role="group" aria-label="Показани компоненти">{COMPONENTS.map(z => <button key={z} type="button" aria-pressed={zones.includes(z)} onClick={() => setZones(old => old.includes(z) ? old.filter(v => v !== z) : [...old, z])}><i style={{ background: COLORS[z] }} />{z}</button>)}</div>
        <p className="management-muted">{metric === "target_weekly_effective" ? "Приравнени минути за 7 дни — различни от часовете тренировка и измереното време в зона. STR е отделен компонент." : "Цел спрямо текущата 40-дневна база. Това не е прогноза на действителния бъдещ 7/40; базата ще се обновява с изпълнението."}</p>
        <div className="management-chart-wrap"><svg className="management-outlook-chart" viewBox="0 0 900 305" role="img" aria-label={metric === "target_weekly_effective" ? "Целеви приравнен товар по седмици" : "Целеви 7/40 спрямо текущата база"}>
          <title>Дългосрочни цели по компоненти; точните стойности са в таблицата за избраната седмица</title>
          {[0, 1, 2, 3, 4].map(i => <g key={i}><line x1="60" x2="840" y1={y(maximum * i / 4)} y2={y(maximum * i / 4)} stroke="#dde7e9" /><text x="50" y={y(maximum * i / 4) + 4} textAnchor="end">{display(maximum * i / 4)}</text></g>)}
          {weeks.map((week, i) => { const e = events.filter(e => label(e.start_date) <= week.end_date && label(e.end_date) >= week.start_date); return e.length ? <g key={week.start_date}><rect x={x(i) - 5} y="30" width="10" height="230" fill="#e9d7a0" opacity=".4" /><text x={x(i)} y="20" textAnchor="middle">{e.some(e => e.event_type === "MAIN_RACE") ? "★" : e.some(e => e.event_type === "CAMP") ? "Л" : "●"}</text><title>{e.map(e => label(e.name)).join(", ")}</title></g> : null; })}
          {zones.map(z => <g key={z}>{weeks.map((week, i) => { const value = week.components[z]?.[metric]; const previous = i > 0 ? weeks[i - 1].components[z]?.[metric] : null; return value === null || value === undefined ? null : <g key={week.start_date}>{previous !== null && previous !== undefined && <line x1={x(i - 1)} y1={y(previous)} x2={x(i)} y2={y(value)} stroke={COLORS[z]} strokeWidth="2.5" />}<circle cx={x(i)} cy={y(value)} r="3" fill={COLORS[z]}><title>{`${z} · ${shortDate(week.start_date)}: ${display(value)}`}</title></circle></g>; })}</g>)}
          {weeks.filter((_, i) => i % Math.max(1, Math.ceil(weeks.length / 8)) === 0 || i === weeks.length - 1).map(w => <text key={w.start_date} x={x(weeks.indexOf(w))} y="287" textAnchor="middle">{shortDate(w.start_date)}</text>)}
          <line x1={x(Math.min(selected, weeks.length - 1))} x2={x(Math.min(selected, weeks.length - 1))} y1="30" y2="260" stroke="#203f4c" strokeDasharray="4 4" />
        </svg></div>
        <p className="management-muted">★ Основен старт · Л Лагер · ● Контролен старт, тест или недостъпен период</p>
        <label>Разгледай седмица<select value={Math.min(selected, weeks.length - 1)} onChange={e => setSelected(Number(e.target.value))}>{weeks.map((w, i) => <option key={w.start_date} value={i}>{shortDate(w.start_date)} – {shortDate(w.end_date)} · {w.accents.join(", ")}</option>)}</select></label>
        {current && <div className="management-week-context">{current.cycle && <p><strong>{label(current.cycle.name)}</strong> · {label(current.cycle.kind) === "STRESS" ? "Стресов микроцикъл" : label(current.cycle.kind) === "RECOVERY" ? "Разтоварване" : "Тренировъчен блок"} · седмица {label(current.cycle.week)}</p>}<p><strong>{current.phases.map(p => PHASE_LABELS[p] ?? p).join(" → ")}</strong> · Акценти: {current.accents.join(", ")}</p>
          {shownEvents.map((e, i) => <p key={i}>{EVENT[label(e.event_type)] ?? label(e.event_type)}: <strong>{label(e.name)}</strong> · {shortDate(label(e.start_date))} – {shortDate(label(e.end_date))}</p>)}
          <div className="management-table-wrap"><table><thead><tr><th>Компонент</th><th>Целеви товар / 7 дни</th><th>Целеви 7/40</th></tr></thead><tbody>{COMPONENTS.map(z => <tr key={z}><th>{z}</th><td>{display(current.components[z]?.target_weekly_effective ?? null)}</td><td>{display(current.components[z]?.target_index_7_40 ?? null)}</td></tr>)}</tbody></table></div>
        </div>}
        <details className="management-detail"><summary>Как се изчислява динамиката?</summary><p>Целевият индекс за акцентите се умножава по вълната на съответната седмица. Например 1,1 × 1,5 = 1,65. Отделно зададен микроцикъл използва собствения си индекс. Прилагат се правилата за вработване, преход и тейпър. Дневната готовност може да ограничи изпълнението, без да променя показаната цел.</p><p>Седмиците следват началото на мезоцикъла; първата може да е непълна. Показана е средната цел в отрязъка, включително при смяна на периода. Историческата база остава фиксирана към датата на изчисление; бъдещи тренировки не я увеличават изкуствено.</p><p>7/40 = (B50 + целеви седмичен товар / 7) / (B50 + C40). Празна стойност означава недостатъчна реална история. Лагерът е календарен контекст и не разрешава автоматично увеличение на товара.</p></details>
      </>}
    </section>
    <PlanComparison plan={plan} outcomes={outcomes} />
    <Link href="/planning#planning-calendar">Промени стартовете и лагерите в профила →</Link>
  </section>;
}
