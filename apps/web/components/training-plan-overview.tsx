"use client";

import Link from "next/link";
import { TimeAvailability, TimeLimitNotice } from "./planning-time-limit";
import { PlanComparison } from "./plan-comparison";
import { LoadProgressionSummary } from "./load-progression-summary";
import { RaceDurationSummary } from "./race-duration-estimate";
import { MicrocycleVolumes } from "./microcycle-volumes";
import { MesocyclePriorities } from "./mesocycle-priorities";
import { durationHms } from "../lib/duration-format";
import { useState } from "react";
import { isRecord } from "../lib/training-status";
import { COMPONENTS, PHASE_LABELS, type Component, type PlanProjection, type PlanOutcome, type PlanningDraft } from "../lib/training-management";

const COLORS: Record<Component, string> = { Z1: "#317997", Z2: "#33936b", Z3: "#b48211", Z4: "#d46b36", Z5: "#be4968", STR: "#7966ba" };
const EVENT: Record<string, string> = { MAIN_RACE: "Основен старт", CONTROL_RACE: "Контролен старт", CAMP: "Лагер", TEST: "Тест", UNAVAILABLE: "Без тренировки" };
const label = (value: unknown) => String(value ?? "");
const shortDate = (day: string) => day.slice(8, 10) + "." + day.slice(5, 7);
const numeric = (value: unknown): number | null => typeof value === "number" && Number.isFinite(value) ? value : null;
const display = (value: number | null) => value === null ? "—" : durationHms(value);
interface Week { volume_budget_minutes: number | null; cycle?: Record<string, unknown>; start_date: string; end_date: string; accents: string[]; phases: string[]; components: Partial<Record<Component, { target_period_q: number | null }>> }
function outlookWeeks(plan?: PlanProjection): Week[] {
  const outlook = plan?.long_term;
  if (!isRecord(outlook) || outlook.schema_version !== "training-outlook-v1" || !Array.isArray(outlook.weeks)) return [];
  return outlook.weeks.filter(isRecord).map(w => ({ volume_budget_minutes: numeric(w.volume_budget_minutes), cycle: isRecord(w.cycle) ? w.cycle : undefined, start_date: label(w.start_date), end_date: label(w.end_date), accents: Array.isArray(w.accents) ? w.accents.map(label) : [], phases: Array.isArray(w.phases) ? w.phases.map(label) : [], components: Object.fromEntries(COMPONENTS.map(z => {
    const c = isRecord(w.components) && isRecord(w.components[z]) ? w.components[z] : {};
    return [z, { target_period_q: numeric(c.target_period_q) }];
  })) }));
}

export function TrainingPlanOverview({ plan, outcomes, today, stale, currentProfileRevision, volumeContext, sessions, sessionStatus = "Няма актуални съставени сесии. Подготви или обнови седмичната програма." }: { plan?: PlanProjection; outcomes: PlanOutcome[]; today: string; stale?: boolean; currentProfileRevision?: number; volumeContext?: Record<string, unknown>; sessions?: PlanningDraft; sessionStatus?: string }) {
  const [zones, setZones] = useState<Component[]>([...COMPONENTS]);
  const [selected, setSelected] = useState(0);
  const weeks = outlookWeeks(plan);
  const current = weeks[Math.min(selected, weeks.length - 1)];
  const shockSchedule = Array.isArray(weeks[0]?.cycle?.shock_schedule) ? weeks[0].cycle.shock_schedule.filter(isRecord) : [];
  const periodization = isRecord(plan?.periodization) ? plan.periodization : {};
  const phases = Array.isArray(periodization.phases) ? periodization.phases.filter(isRecord) : [];
  const snapshot = isRecord(plan?.input_snapshot) ? plan.input_snapshot : {};
  const calendar = isRecord(snapshot.calendar) ? snapshot.calendar : {};
  const events = Array.isArray(calendar.events) ? calendar.events.filter(isRecord) : [];
  const shownEvents = current ? events.filter(e => label(e.start_date) <= current.end_date && label(e.end_date) >= current.start_date) : [];
  const maximum = Math.max(1, ...weeks.flatMap(w => zones.map(z => w.components[z]?.target_period_q ?? 0))) * 1.1;
  const x = (index: number) => 90 + index * 740 / Math.max(1, weeks.length - 1);
  const y = (value: number) => 260 - value * 220 / maximum;
  return <section className="management-overview" aria-label="Дългосрочна подготовка">
    <LoadProgressionSummary plan={plan}/>
    {volumeContext && <section className="management-panel"><h2>Обем на подготовката</h2>
      <p className="management-muted">Актуални цели от записания профил · версия {currentProfileRevision}. Промените в профила се отразяват тук при отваряне; седмичните тренировки се подготвят отделно.</p>
      <div className="management-metrics"><div><small>Историческа основа за програмата</small><strong>{numeric(volumeContext.historical_training_weekly_minutes) === null ? "—" : durationHms(Number(volumeContext.historical_training_weekly_minutes))}</strong><span>средно за 7 дни</span></div><TimeAvailability context={volumeContext}/></div>
      {typeof volumeContext.available_weekly_minutes === "number" && Number(volumeContext.available_weekly_minutes) < Number(volumeContext.historical_training_weekly_minutes) && <p className="management-notice">Записаното свободно време е по-малко от историческия обем и ограничава програмата. <Link href="/planning">Провери дните и минутите в профила →</Link></p>}
      <TimeLimitNotice context={volumeContext}/>
      <p className="management-muted">Приравненият обем по зони е планова цел. Часовниковото време се показва само за вече съставените сесии и включва загряване, паузи и разпускане.</p>
    </section>}
    <section className="management-panel"><h2>Посока на подготовката</h2><RaceDurationSummary value={plan?.race_duration ?? plan?.parameters?.race_duration}/>
      {!plan ? <p>Запази профил за планиране, за да видиш разпределението според целите и календара.</p> : <>
        {stale && <p className="management-notice">Показана е запазената версия. Преизчисли програмата от седмичния изглед, за да включиш последните данни.</p>}
        <div className="management-phase-list">{phases.map((p, i) => <div key={i} className={label(p.start_date) <= today && label(p.end_date) >= today ? "is-current" : ""}><span>{shortDate(label(p.start_date))} – {shortDate(label(p.end_date))}</span><strong>{PHASE_LABELS[label(p.kind)] ?? label(p.kind)}</strong><small>{label(p.days)} дни</small></div>)}</div>
      </>}
    </section>
    <section className="management-panel"><h2>Динамика по зони</h2><p className="management-muted">Целеви товар според периода, акцентите и разтоварването. Дневната доза се уточнява отделно според готовността.</p>
      {isRecord(plan?.long_term) && plan.long_term.limited === true && <p className="management-notice">Данните за дневната готовност са непълни или неактуални. Показани са зададените цели; допустимите тренировки се проверяват отделно след обновяване на данните.</p>}
      {weeks.length === 0 ? <p>Няма бъдещи седмици за показване. Провери периода на подготовката в профила.</p> : <>
        <div className="management-zone-legend" role="group" aria-label="Показани компоненти">{COMPONENTS.map(z => <button key={z} type="button" aria-pressed={zones.includes(z)} onClick={() => setZones(old => old.includes(z) ? old.filter(v => v !== z) : [...old, z])}><i style={{ background: COLORS[z] }} />{z}</button>)}</div>
        <p className="management-muted">Приравнен обем Q за точните дати на микроцикъла, в ч:мм:сс. Графиката и таблицата използват едни и същи стойности. Непълният микроцикъл съдържа само показаните дни. Това са цели преди проверката на ежедневната готовност.</p>
        <div className="management-chart-wrap"><svg className="management-outlook-chart" viewBox="0 0 900 305" role="img" aria-label="Приравнен обем по микроцикли">
          <title>Дългосрочни цели по компоненти; точните стойности са в таблицата по микроцикли</title>
          {[0, 1, 2, 3, 4].map(i => <g key={i}><line x1="90" x2="840" y1={y(maximum * i / 4)} y2={y(maximum * i / 4)} stroke="#dde7e9" /><text x="80" y={y(maximum * i / 4) + 4} textAnchor="end">{display(maximum * i / 4)}</text></g>)}
          {weeks.map((week, i) => { const e = events.filter(e => label(e.start_date) <= week.end_date && label(e.end_date) >= week.start_date); return e.length ? <g key={week.start_date}><rect x={x(i) - 5} y="30" width="10" height="230" fill="#e9d7a0" opacity=".4" /><text x={x(i)} y="20" textAnchor="middle">{e.some(e => e.event_type === "MAIN_RACE") ? "★" : e.some(e => e.event_type === "CAMP") ? "Л" : "●"}</text><title>{e.map(e => label(e.name)).join(", ")}</title></g> : null; })}
          {zones.map(z => <g key={z}>{weeks.map((week, i) => { const value = week.components[z]?.target_period_q; const previous = i > 0 ? weeks[i - 1].components[z]?.target_period_q : null; return value === null || value === undefined ? null : <g key={week.start_date}>{previous !== null && previous !== undefined && <line x1={x(i - 1)} y1={y(previous)} x2={x(i)} y2={y(value)} stroke={COLORS[z]} strokeWidth="2.5" />}<circle cx={x(i)} cy={y(value)} r="3" fill={COLORS[z]}><title>{`${z} · ${shortDate(week.start_date)}: ${display(value)}`}</title></circle></g>; })}</g>)}
          {weeks.filter((_, i) => i % Math.max(1, Math.ceil(weeks.length / 8)) === 0 || i === weeks.length - 1).map(w => <text key={w.start_date} x={x(weeks.indexOf(w))} y="287" textAnchor="middle">{shortDate(w.start_date)}</text>)}
          <line x1={x(Math.min(selected, weeks.length - 1))} x2={x(Math.min(selected, weeks.length - 1))} y1="30" y2="260" stroke="#203f4c" strokeDasharray="4 4" />
        </svg></div>
        <p className="management-muted">★ Основен старт · Л Лагер · ● Контролен старт, тест или недостъпен период</p>
        <label>Разгледай седмица<select value={Math.min(selected, weeks.length - 1)} onChange={e => setSelected(Number(e.target.value))}>{weeks.map((w, i) => <option key={w.start_date} value={i}>{shortDate(w.start_date)} – {shortDate(w.end_date)} · {w.cycle?.kind === "RECOVERY" ? "Разтоварване" : w.accents.join(", ")}</option>)}</select></label>
        {current && <div className="management-week-context">{current.cycle && <p><strong>{label(current.cycle.name)}</strong> · {label(current.cycle.kind) === "STRESS" ? "Стресов микроцикъл" : label(current.cycle.kind) === "RECOVERY" ? "Разтоварване" : "Тренировъчен блок"} · седмица {label(current.cycle.week)}</p>}<p><strong>{current.phases.map(p => PHASE_LABELS[p] ?? p).join(" → ")}</strong> · {current.cycle?.focus_role === "RECOVERY_SUPPORT" ? "Допълващо поддържане" : "Акценти"}: {current.accents.join(", ") || "няма"}</p>
          {Array.isArray(current.cycle?.mesocycle_accents) && <p>Водещи за мезоцикъла: <strong>{current.cycle.mesocycle_accents.map(label).join(", ")}</strong> · {shortDate(label(current.cycle.mesocycle_start))} – {shortDate(label(current.cycle.mesocycle_end))}</p>}
          <MesocyclePriorities cycle={current.cycle}/>
          {typeof current.cycle?.reason === "string" && <p className="management-muted">{current.cycle.reason}{current.cycle.focus_role === "RECOVERY_SUPPORT" ? " Изборът е условен по наличните данни и се проверява отново при генериране на седмицата." : ""}</p>}
          {shownEvents.map((e, i) => <p key={i}>{EVENT[label(e.event_type)] ?? label(e.event_type)}: <strong>{label(e.name)}</strong> · {shortDate(label(e.start_date))} – {shortDate(label(e.end_date))}</p>)}
        </div>}
        <details className="management-detail"><summary>Как се изчислява динамиката?</summary><p>Измереният приравнен обем е началната точка за дозиране. Опората за прираста запазва историята между експертните граници; под или над тях използва съответната граница. При липсваща надеждна история се използва експертен ориентир, който сам не разрешава тренировка.</p><p>Мезоцикълът определя акцентите, а вълната разпределя обема между натоварващите и възстановителните седмици. Реалните тренировки се ограничават допълнително според 7/40, възстановяването, методите и наличното време. Пропуснатият обем не се наваксва автоматично. За силата няма автоматичен годишен прираст.</p><p>Бъдещите цели не се записват като изпълнени тренировки. Празна стойност означава, че няма определена цел за директния приравнен обем; тя не се заменя с друг вид товар.</p></details>
      </>}
    </section>
    {weeks.length > 0 && <MicrocycleVolumes weeks={weeks} sessions={sessions} status={sessionStatus}/> }
    {shockSchedule.length > 0 && <details className="management-panel"><summary>Ударни микроцикли и разтоварване</summary><ul>{shockSchedule.map((s,i)=><li key={i}><strong>{PHASE_LABELS[label(s.period)] ?? label(s.period)}</strong>: {s.status === "UNAVAILABLE" ? label(s.reason) : `${s.status === "MANUAL" ? "Ръчно зададен" : "Планиран"} · ${label(s.start_date)} – ${label(s.end_date)}${s.recovery_end ? ` · разтоварване до ${label(s.recovery_end)}` : ""}`}</li>)}</ul><p>Това са календарни намерения. Дневната готовност, наличният обем и ограниченията на методите определят дали ударната доза може да бъде изпълнена.</p></details>}
    <details className="management-panel"><summary>План и реално изпълнение</summary><PlanComparison plan={sessions ?? plan} outcomes={outcomes} /></details>
    <Link href="/planning#planning-calendar">Промени стартовете и лагерите в профила →</Link>
  </section>;
}
