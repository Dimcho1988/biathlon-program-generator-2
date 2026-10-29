"use client";

import { useId, useState, type ReactNode } from "react";
import { durationHms } from "../lib/duration-format";
import { daySessions, type ActivePlanRecord, type DraftDay, type DraftSession, type PlanChange } from "../lib/training-management";
import { sessionSummary, SPORT_LABELS } from "../lib/training-visuals";
import { BlockExecutionRange } from "./block-execution-range";
import { speedBasisLabel } from "../lib/speed-basis";
import { PreferredSpeed } from "./running-speed-unit";
import { SyncActionForm } from "./sync-action-form";
import { TrainingPlanSummary } from "./training-plan-summary";
import { TrainingPlanWeek } from "./training-plan-week";
import { proposalHasIncompleteLoad, proposedWeekMode, WEEK_MODE_LABEL } from "../lib/week-mode";
import { planWeekView, selectedPlanWeek } from "../lib/plan-week-view";

const dateLabel = (date: string) => new Date(`${date}T12:00:00Z`).toLocaleDateString("bg-BG", { day: "numeric", month: "long", timeZone: "UTC" });

function WorkTargets({ session }: { session: DraftSession }) {
  const seen = new Set<string>();
  const targets = session.blocks.filter(block => {
    if (block.kind !== "WORK" || (!block.target_speed_range_kmh && block.target_speed_kmh == null && !block.hr_reference_range_bpm && block.target_hr_bpm == null)) return false;
    const key = JSON.stringify([block.zone, block.target_speed_range_kmh, block.target_speed_kmh, block.hr_reference_range_bpm, block.target_hr_bpm, block.primary_control, block.speed_basis, block.distance_basis, block.distance_range_m]);
    if (seen.has(key)) return false;
    seen.add(key); return true;
  });
  return targets.length > 0 && <ul className="management-proposal-targets" aria-label="Интензивност на работните части">{targets.map((block, index) => <li key={index}>
    <strong>{block.zone}:</strong> {block.target_speed_range_kmh ? <BlockExecutionRange block={block} sport={session.sport}/> : block.target_speed_kmh != null && <>{speedBasisLabel(block)}: <PreferredSpeed speed={block.target_speed_kmh} sport={session.sport}/>. </>}
    {block.hr_reference_range_bpm ? ` ${block.hr_reference_range_bpm.join("–")} уд./мин` : block.target_hr_bpm != null && ` ${block.target_hr_bpm} уд./мин`}
  </li>)}</ul>;
}

function TaskSummary({ day, title, minutes }: { day?: DraftDay; title: string | null; minutes: number }) {
  const sessions = day ? daySessions(day) : [];
  return sessions.length ? <>{sessions.map((session, index) => <div key={index}>
    <p><strong>{session.title}</strong> · {SPORT_LABELS[session.sport] ?? session.sport} · {durationHms(session.total_minutes)}</p>
    <p className="management-muted">{sessionSummary(session)}</p>
    <WorkTargets session={session}/>
  </div>)}</> : <p>{title ?? "Няма задача в този вариант"}{minutes > 0 && ` · ${durationHms(minutes)}`}</p>;
}

function ChangedDay({ change, before, after, renderDay }: {
  change: PlanChange; before?: DraftDay; after?: DraftDay; renderDay: (day: DraftDay) => ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);
  return <li>
    <h4>{dateLabel(change.date)}</h4>
    <p>{change.reason}</p>
    <div className="management-proposal-pair">
      <div><h5>Утвърдено</h5><TaskSummary day={before} title={change.before} minutes={change.before_minutes}/></div>
      <div><h5>Предложено</h5><TaskSummary day={after} title={change.after} minutes={change.after_minutes}/></div>
    </div>
    <details onToggle={event => setExpanded(event.currentTarget.open)}>
      <summary>Сравни задачите за {dateLabel(change.date)}</summary>
      {expanded && <div className="management-proposal-pair">
        <div><h5>Утвърдена задача</h5>{before ? renderDay(before) : <p>Няма задача в утвърдения период.</p>}</div>
        <div><h5>Предложена задача</h5>{after ? renderDay(after) : <p>Задачата отпада от предложения вариант.</p>}</div>
      </div>}
    </details>
  </li>;
}

/** Mounted with the record revision as its key: a new proposal needs a new review. */
export function PlanProposalReview({ record, canEdit, busy, symptomHold, onApprove, renderDay, today }: {
  record: ActivePlanRecord; canEdit: boolean; busy: boolean; symptomHold: boolean;
  onApprove: () => void; renderDay: (day: DraftDay) => ReactNode; today: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const [fullPlan, setFullPlan] = useState(false);
  const [selectedWeek, setSelectedWeek] = useState<string>();
  const id = useId();
  const p = record.payload;
  const proposal = p.proposal;
  if (!proposal) return null;
  const week = selectedPlanWeek(proposal,today,selectedWeek);
  const schedule = planWeekView(proposal,week);
  const proposalMode = proposedWeekMode(record);
  const blocked = !canEdit ? "Имате достъп само за преглед. Утвърждаването изисква право за редакция на плана."
    : p.status === "PAUSED" ? "Програмата е на пауза. Продължи с текущите настройки и прегледай актуалното предложение."
    : p.status !== "REVIEW_REQUIRED" ? "В момента няма предложение, което може да бъде утвърдено."
    : record.stale ? "Данните са променени. Избери „Обнови сега“ и прегледай актуалното предложение."
    : symptomHold ? "Първо запиши актуална сутрешна оценка и обнови програмата."
    : proposalHasIncompleteLoad(proposal) ? `Утвърждаването е задържано заради непълна история на натоварването. Същото ограничение допуска само леки предложения${proposalMode && proposalMode !== p.mode ? " и задържа предложената смяна на режима" : ""}, докато непокритите участъци бъдат изяснени.`
    : proposal.activation_eligible !== true ? "Утвърждаването е задържано. Прегледай предупрежденията и условията по-горе и обнови необходимите данни."
    : busy ? "Изчакай текущото обновяване, преди да утвърдиш предложението." : null;
  return <section className="management-panel management-proposal" aria-label="Предложени промени">
    <div className="management-proposal-heading">
      <div><h3>Има предложени промени</h3><p>{p.reason}</p>
        <p className="management-muted">Предложението още не е приложено. Утвърденият график е показан по-долу.</p>
      </div>
      <button type="button" className="action-button secondary" aria-expanded={expanded} aria-controls={id}
        onClick={() => { setExpanded(!expanded); setFullPlan(false); }}>{expanded ? "Затвори прегледа" : "Прегледай"}</button>
    </div>
    <div id={id} hidden={!expanded}>{expanded && <>
      <p><strong>Предложен период:</strong> {dateLabel(proposal.start_date)} – {dateLabel(proposal.end_date)}.</p>
      {proposalMode && <p><strong>Режим в предложението:</strong> {WEEK_MODE_LABEL[proposalMode]}.</p>}
      {proposalMode && proposalMode !== p.mode && <p><strong>Смяна на режима:</strong> {p.mode === "AUTO" ? "адаптивна → фиксирана" : "фиксирана → адаптивна"} седмица.</p>}
      {p.changes.length ? <><h4>Засегнати дни: {p.changes.length}</h4>
        <ol className="management-proposal-changes">{p.changes.map(change => <ChangedDay key={change.date} change={change}
          before={p.plan.days.find(day => day.date === change.date)} after={proposal.days.find(day => day.date === change.date)} renderDay={renderDay}/>)}</ol>
      </> : <p>Няма отчетени промени в тренировъчните задачи. Предложението изисква преглед по посочената причина.</p>}
      <details className="management-detail" onToggle={event => setFullPlan(event.currentTarget.open)}>
        <summary>Всички дни и обобщение на предложението</summary>
        {fullPlan && <><p>Прогнозите за Recovery и бюджетите тук се отнасят до предложения вариант.</p>
          <TrainingPlanSummary plan={proposal} qExecution={p.q_execution} today={today} selectedWeek={selectedWeek} onWeekChange={setSelectedWeek}/><TrainingPlanWeek days={schedule.days} period={week} forecastDates={schedule.forecastDates} programmeLabel="Предложение" today={today} renderDay={renderDay}/></>}
      </details>
      <div className="management-proposal-decision">
        <p>С „Утвърди предложението“ прилагаш целия прегледан вариант за посочения период{proposalMode && proposalMode !== p.mode ? ", включително новия режим" : ""}.</p>
        {blocked && <p id={`${id}-blocked`} role="status">{blocked}</p>}
        <div className="management-actions">
          <button type="button" className="action-button" disabled={blocked !== null} aria-describedby={blocked ? `${id}-blocked` : undefined} onClick={onApprove}>Утвърди предложението</button>
          {canEdit && !busy && !symptomHold && p.status === "REVIEW_REQUIRED" && proposal.activation_eligible !== true && <SyncActionForm scope="FULL" returnTo="/management" label="Обнови тренировките"/>}
        </div>
      </div>
    </>}</div>
  </section>;
}
