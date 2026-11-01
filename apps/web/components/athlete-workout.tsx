"use client";

import { durationHms } from "../lib/duration-format";
import { type DraftDay, type DraftSession, type SessionBlock } from "../lib/training-management";
import { componentColor, componentLabel, workoutTitle } from "../lib/training-visuals";
import { phaseDose, phaseHeading, phaseIntensity, phaseName, shortWork, workoutStructure, type WorkoutPhase } from "../lib/workout-prescription";
import { useRunningSpeedUnit } from "./running-speed-unit";
import { SessionRole } from "./session-role";
import { SessionAccent, WorkoutProfile } from "./workout-profile";

function PhaseLine({phase, session, rest}: {phase: WorkoutPhase; session: DraftSession; rest?: boolean}) {
  const unit = useRunningSpeedUnit();
  const {block} = phase;
  const targets = phaseIntensity(block, session, unit);
  return <div className={rest ? "workout-between" : "workout-phase-line"}>
    <span><strong>{rest ? phaseName(block) : phaseHeading(phase)}</strong> · {phaseDose(phase)}</span>
    {targets.length > 0 && <small>{targets.join(" · ")}</small>}
  </div>;
}

function reserveNote(blocks: SessionBlock[], session: DraftSession) {
  const work = blocks.filter(block => block.kind === "WORK" && block.zone !== "STR" && block.zone !== "NMS");
  const reserves = new Map<string, Set<number>>();
  for (const block of work) {
    const n = block.reserve_repetitions;
    if (typeof n !== "number" || !Number.isInteger(n) || n < 1) continue;
    if (!reserves.has(block.zone)) reserves.set(block.zone, new Set());
    reserves.get(block.zone)!.add(n);
  }
  // Older single-method plans may keep the interval reserve only in the dose profile.
  const frozen = session.dose_evidence?.effort_profile?.reserve_repetitions;
  if (!reserves.size && !session.dose_evidence?.combined_budgets?.length && work.some(b => b.repetition != null)
    && typeof frozen === "number" && Number.isInteger(frozen) && frozen > 0) reserves.set(session.zone, new Set([frozen]));
  return [...reserves].flatMap(([zone, values]) => {
    if (values.size !== 1) return [];
    const [n] = values;
    return ["След последната отсечка в " + zone + " запази резерв за още " + (n === 1 ? "една отсечка" : n + " отсечки") + " със същата скорост и техника."];
  });
}

function savedEffortCues(session: DraftSession): string[] {
  const cues = new Map<string, string>();
  for (const block of session.blocks) {
    if (block.kind !== "WORK" || block.zone === "STR" || cues.has(block.zone)) continue;
    const phrase = block.instructions?.trim().split(/(?<=[.!?])\s+(?=[А-ЯA-Z])/u)[0];
    if (!phrase || phrase.length > 160 || /^Виж част|резерв|Tmax|Q\/E|ролеви бюджет/iu.test(phrase)) continue;
    if (shortWork(block) && /пулс|уд\.\/?мин|HRmax/iu.test(phrase)) continue;
    cues.set(block.zone, phrase.replace(/;\s*/g, ", "));
  }
  return [...cues].slice(0, 2).map(([zone, cue]) => cues.size > 1 ? zone + " · " + cue : cue);
}

function executionNotes(session: DraftSession, phases: WorkoutPhase[]) {
  const blocks = session.blocks;
  const strength = blocks.some(b => b.kind === "WORK" && b.zone === "STR") || !!session.dose_evidence?.strength_prescription;
  const nms = blocks.some(b => b.zone === "NMS");
  const repeated = phases.some(p => p.block.kind === "WORK" && p.block.zone !== "STR"
    && (p.count > 1 || (p.block.repetition ?? 0) > 1));
  const easy = blocks.filter(b => b.kind === "WORK").every(b => b.zone === "Z1" || b.zone === "Z2");
  const notes: string[] = [];
  if (!phases.length && !strength) return ["Към запазената тренировка няма указания за отделните части."];
  if (blocks.some(b => b.kind === "WARMUP")) notes.push("Започни загрявката спокойно и постепенно увеличи ритъма в зададената зона.");
  if (repeated) {
    notes.push(...savedEffortCues(session));
    notes.push("Започни първата отсечка с овладяно усилие. Ако тръгнеш прекалено бързо, коригирай скоростта още в нея.");
  } else if (!strength && !nms) {
    notes.push(easy ? "Поддържай равномерен ритъм и спокойно дишане. След разпускането трябва да усещаш, че можеш да продължиш."
      : "Достигни постепенно зададената интензивност и следвай границите за всяка част. Завърши с контролирано усилие и запазена техника.");
  }
  if (strength) notes.push("Избери тежест, с която изпълняваш предписаните повторения с добра техника. Завърши всяка серия с резерв и запази амплитудата на движението.");
  if (nms) notes.push("При ускоренията увеличавай скоростта плавно. Започвай следващото повторение с възстановена координация и приключи серията, ако техниката се влоши.");
  const reserves = repeated ? reserveNote(blocks, session) : [];
  notes.push(...reserves);
  if (repeated && !reserves.length) notes.push("След последната отсечка трябва да усещаш умора, но да можеш да запазиш техниката и зададеното темпо.");
  const shorten = repeated || nms ? "можеш значително да намалиш броя на оставащите отсечки"
    : strength ? "можеш да намалиш броя на сериите" : "можеш да съкратиш основната част";
  notes.push("Ако възстановяването е непълно или умората нараства прекалено рано, " + shorten + ".");
  const gap = session.dose_evidence?.planned_gap_hours;
  if (typeof gap === "number" && gap > 0) notes.push("Остави поне " + durationHms(gap * 60) + " ч между двете прагови тренировки. Преди втората прецени как се чувстваш след първата.");
  if (session.race_course_guidance?.note) notes.push(session.race_course_guidance.note);
  return notes;
}

function whyToday(session: DraftSession, day: DraftDay) {
  const evidence = session.dose_evidence;
  const zone = session.zone;
  const accents = Array.isArray(day.cycle?.accents) ? day.cycle.accents : [];
  const role = evidence?.resolved_role ?? session.purpose;
  const component = zone === "STR" ? "силовата подготовка" : "работата в " + componentLabel(zone);
  const reasons = [];
  if (day.taper) reasons.push("Предстои състезание. Тази доза запазва тренировъчния ритъм при намален обем.");
  else if (day.period === "RE_ENTRY" || evidence?.reentry_dose_policy === "MAINTENANCE_ROLE_BUDGET") reasons.push("Тренировката е част от постепенното връщане към натоварване.");
  else if (role === "RECOVERY") reasons.push("Леката работа е избрана за възстановяване между по-натоварващите занимания.");
  else if (day.cycle?.kind === "RECOVERY") reasons.push("В разтоварващия микроцикъл е предвидена по-лека доза за " + component + ".");
  else if (role === "ACTIVATION") reasons.push("Кратката работа подготвя за предстоящото натоварване.");
  else if (evidence?.selection?.accent === true || accents.includes(zone)) reasons.push("В този микроцикъл акцентът е върху " + component + ".");
  else if (["MAINTENANCE", "MAINTAIN", "SUPPORTING"].includes(role)) reasons.push("Днес е предвидена поддържаща доза за " + component + ".");
  else reasons.push("Тренировката разпределя част от планирания за микроцикъла обем за " + component + ".");
  const budget = day.load_budget?.components?.[zone as keyof typeof day.load_budget.components];
  if (budget?.prescription_basis === "LONG_TERM_MICROCYCLE_Q" && typeof budget.remaining_target_q === "number" && budget.remaining_target_q > 0) {
    reasons.push("При разпределянето остават " + durationHms(budget.remaining_target_q) + " по Q до целта за " + componentLabel(zone) + ".");
  }
  if (typeof evidence?.readiness_dose_factor === "number" && evidence.readiness_dose_factor < 1) {
    reasons.push("Планираният обем е намален според прогнозното възстановяване за деня.");
  }
  return reasons.join(" ");
}

export function AthleteWorkout({day, session, position}: {day: DraftDay; session: DraftSession; position?: string}) {
  const groups = workoutStructure(session);
  const phases = groups.flatMap(group => group.phases);
  const bank = session.dose_evidence?.strength_prescription;
  return <article className="management-day athlete-workout" style={{borderLeftColor:componentColor(session.zone)}}>
    <header><div><p className="management-day-date">{new Date(day.date + "T12:00:00Z").toLocaleDateString("bg-BG", {weekday:"long",day:"numeric",month:"long",timeZone:"UTC"})}{position && " · " + position}</p>
      <h3>{workoutTitle(session)}</h3></div><SessionAccent zone={session.zone}/></header>
    <div className="management-session-visual"><p className="management-session-total"><strong>{session.execution_range_policy && "до "}{durationHms(session.total_minutes)}</strong> общо</p><WorkoutProfile session={session}/></div>
    {groups.length > 0 && <ol className="workout-prescription" aria-label="Последователност и интензивност на тренировката">{groups.map((group, index) =>
      <li key={index} style={{borderInlineStartColor:componentColor(group.phases[0].block.zone)}}>
        {group.repeats > 1 && <strong>Повтори последователността {group.repeats} пъти</strong>}
        {group.phases.map((phase, part) => <div className="workout-dose-part" key={part}>
          <PhaseLine phase={phase} session={session}/>
          {phase.block.zone === "STR" && typeof phase.block.reserve_repetitions === "number" && <small>Резерв от {phase.block.reserve_repetitions} {phase.block.reserve_repetitions === 1 ? "повторение" : "повторения"} със същата тежест.</small>}
          {phase.between && <PhaseLine phase={{block:phase.between,count:1}} session={session} rest/>}
        </div>)}
      </li>)}</ol>}
    {bank && !phases.some(p => p.block.exercise_id) && <ul>{bank.exercises.map(e => <li key={e.id}>{e.name} · {e.sets} × {e.reps} повторения</li>)}</ul>}
    <details className="athlete-workout-notes"><summary>Бележки за изпълнението</summary><ul>{executionNotes(session, phases).map(note => <li key={note}>{note}</li>)}</ul></details>
    <details className="athlete-workout-notes"><summary>Защо тази тренировка сега?</summary><SessionRole session={session} day={day}/><p>{whyToday(session, day)}</p></details>
  </article>;
}
