"use client";

import Link from "next/link";
import { changeActivePlan, newerActivePlan, PLAN_CHANGED_NOTICE } from "../lib/active-plan-request";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { durationHms } from "../lib/duration-format";
import { componentColor, sessionSummary } from "../lib/training-visuals";
import { SessionAccent, WorkoutProfile } from "./workout-profile";
import { isRecord } from "../lib/training-status";
import { ActiveTrainingPlan } from "./active-training-plan";
import { TrainingPlanWeek } from "./training-plan-week";
import { PlanComparison } from "./plan-comparison";
import { TrainingPlanSummary } from "./training-plan-summary";
import { TrainingPlanOverview } from "./training-plan-overview";
import { MesocyclePriorities } from "./mesocycle-priorities";
import { managementGuidance, hasProgramDays } from "../lib/management-guidance";
import { SyncActionForm } from "./sync-action-form";
import { SyncStatusPanel } from "./sync-status-panel";
import type { SyncState } from "../lib/sync";
import {
  daySessions, CAPACITY_LABELS, COMPONENTS, PHASE_LABELS, parseDraftRecord, parseDrafts,
  parseActivePlanResponse, type ActivePlanResponse, type ManagementOutlook, type DraftDay, type DraftRecord, type ManagementProfileResponse,
} from "../lib/training-management";

const number = (value: unknown, digits = 1) => typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("bg-BG", { maximumFractionDigits: digits }) : "—";
const dateLabel = (day: string) => new Date(`${day}T12:00:00Z`).toLocaleDateString("bg-BG", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });
const datePlus = (day: string, days: number) => { const value = new Date(`${day}T12:00:00Z`); value.setUTCDate(value.getUTCDate() + days); return value.toISOString().slice(0, 10); };
const STATUS_LABELS: Record<string, string> = { TRAINING: "Тренировка", REST: "Почивка", EXISTING_ACTIVITY: "Има изпълнена активност", UNAVAILABLE: "Няма свободно време", RACE: "Състезание", REVIEW_REQUIRED: "Нужен е преглед", SKIPPED: "Пропусната тренировка" };
const LIMIT_LABELS: Record<string, string> = { WHOLE_STRUCTURE_DOSE_FRACTION: "Общ дозов таван за всички работни части", COMPONENT_SLOT_ALLOCATION: "Разпределен товар за тази сесия", TECHNICAL_SESSION_CEILING: "Техническа граница на сесията, не оценка на свободното време", ACTUAL_SPORT_EXPOSURE: "Историческа поносимост за конкретното средство", ACTUAL_SPORT_SESSION_EXPOSURE: "Най-дълга скорошна сесия за това средство × вълна", RESERVE_LONG_SESSION_TIME: "Запазено време за дългата тренировка", METHOD_WORK_CAP: "Максимална основна работа за метода", DAILY_AVAILABLE_WORK: "Оставащо време след загрявка и разпускане", REMAINING_WEEKLY_WORK: "Оставащ седмичен обем за основна работа", LOW_ABSOLUTE_RECOVERY_CAP: "Лимит на възстановителната работа", TAPER_DAILY_WORK_CAP: "Лимит за предсъстезателно разтоварване", ROLLING_7_40_COMPONENT_BUDGET: "Оставащ компонентен бюджет", RACE_DURATION_WORK_CAP: "Граница според дисциплината", RESERVE_KEY_SESSION_TIME: "Запазено време за ключовите сесии" };
const FALLBACK_LABELS: Record<string, string> = {
  NO_INDIVIDUAL_SPEED_CURVE: "Все още няма индивидуално калибрирана крива скорост–време.",
  EXPLORATORY_OR_NONMAXIMAL_TESTS: "Наличните тестове са ориентировъчни или не са максимални.",
  INSUFFICIENT_INDEPENDENT_TEST_DURATIONS: "Нужни са поне две различни продължителности на надеждни тестове.",
  HRMAX_REQUIRED_FOR_HR_SPEED_MAPPING: "Липсва максимален пулс за връзката пулс–скорост.",
  STALE_HR_SPEED_INDEX: "Индексът за връзката пулс–скорост няма достатъчно скорошни наблюдения.",
  INSUFFICIENT_COMPARABLE_INDEX_OBSERVATIONS: "Няма достатъчно съпоставими наблюдения на индекса в тази зона.",
  HR_MAPPING_USES_EXPERT_ANCHOR: "Преобразуването от пулс към скорост използва експертна опорна стойност.",
  OUTSIDE_OBSERVED_TEST_DURATION_SUPPORT: "Избраната продължителност е извън диапазона, подкрепен от индивидуалните тестове.",
  OUTSIDE_HR_SPEED_PREDICTION_RANGE: "Избраната интензивност е извън надеждния диапазон на връзката пулс–скорост.",
};

function PeriodizationTable({ value }: { value: unknown }) {
  if (!isRecord(value) || !Array.isArray(value.phases)) return null;
  const phases = value.phases.filter(isRecord);
  return <><h3>Разпределение на периодите</h3><div className="management-table-wrap"><table><thead><tr><th>Период</th><th>От</th><th>До</th><th>Дни</th></tr></thead><tbody>{phases.map((phase, index) => <tr key={index}><th>{PHASE_LABELS[String(phase.kind)] ?? String(phase.kind)}</th><td>{String(phase.start_date)}</td><td>{String(phase.end_date)}</td><td>{number(phase.days, 0)}</td></tr>)}</tbody></table></div>
    <details><summary>Защо са избрани тези периоди?</summary><ul>{phases.map((phase, index) => <li key={index}><strong>{PHASE_LABELS[String(phase.kind)] ?? String(phase.kind)}:</strong> {String(phase.reason ?? "")}</li>)}</ul></details></>;
}

function SingleSessionCard({ day, expanded = false }: { day: DraftDay; expanded?: boolean }) {
  const session = day.session;
  const evidence = session?.dose_evidence;
  return <article className={`management-day ${session ? "has-session" : ""}`} style={session ? { borderLeftColor: componentColor(session.zone) } : undefined}>
    <header><div><p className="management-day-date">{dateLabel(day.date)}</p><h3>{session?.title ?? (day.time_limit_exhausted ? "Изчерпан лимит за време" : STATUS_LABELS[day.status]) ?? day.status}</h3></div>
      <span className="management-badge">{PHASE_LABELS[day.period] ?? day.period}{day.taper ? " · тейпър" : ""}</span></header>
    {session && <div className="management-session-visual"><div className="management-session-total"><strong>{durationHms(session.total_minutes)}</strong> общо · {durationHms(session.main_work_minutes)} основна работа <SessionAccent zone={session.zone}/></div><p className="workout-summary">{sessionSummary(session)}</p><WorkoutProfile session={session}/><p className="workout-profile-note">Ширина: продължителност · височина: целева зона Z1–Z5. Силата е отделен компонент.</p></div>}
    <p>{day.explanation}</p>
    <MesocyclePriorities cycle={isRecord(day.cycle) ? day.cycle : undefined}/>
    {session && <details className="management-execution" open={expanded}><summary>Как да изпълня тренировката</summary><ol className="management-blocks">{session.blocks.map((block, index) => <li key={`${block.kind}-${index}`} style={{ borderLeft: `3px solid ${componentColor(block.zone)}` }}><div><strong>{block.label}</strong><span>{durationHms(block.duration_min)} · {block.zone}</span></div><p>{block.instructions}</p>
      {(block.target_hr_bpm !== null || block.target_speed_kmh !== null) && <small>{block.target_hr_bpm !== null ? `${number(block.target_hr_bpm, 0)} уд./мин` : ""}{block.target_hr_bpm !== null && block.target_speed_kmh !== null ? " · " : ""}{block.target_speed_kmh !== null ? `${number(block.target_speed_kmh)} km/h${block.primary_control === "EFFORT_AND_QUALITY" && block.speed_basis !== "FLAT_EQUIVALENT" ? " · зададена скорост" : " · равнинна референция, не темпо по наклон"}` : ""}</small>}
    </li>)}</ol></details>}
    <details className="management-detail"><summary>Защо тази задача и доза?</summary>
      {evidence && <><p><strong>Основа: {CAPACITY_LABELS[evidence.capacity_source] ?? evidence.capacity_source}.</strong></p><p>{evidence.explanation}</p>
        <dl className="management-facts"><div><dt>{evidence.capacity_source === "STRENGTH_METHOD_PROFILE" ? "Работна граница на силовия профил" : "Непрекъсната устойчивост"}</dt><dd>{durationHms(evidence.capacity_minutes)}</dd></div><div><dt>Използван дял от общия работен капацитет</dt><dd>{number((evidence.applied_structure_fraction ?? evidence.fraction) * 100)}%</dd></div><div><dt>Първоначално поискана работа</dt><dd>{durationHms(evidence.requested_work_minutes)}</dd></div><div><dt>Предписана основна работа</dt><dd>{durationHms(evidence.prescribed_work_minutes)}</dd></div></dl>
        {evidence.limits.length > 0 && <><h4>Приложени ограничения</h4><ul>{evidence.limits.map((limit, index) => <li key={`${limit.code}-${index}`}>{LIMIT_LABELS[limit.code] ?? limit.code}: {durationHms(limit.limit_minutes)}</li>)}</ul></>}
        {evidence.fallback_reasons.length > 0 && <><h4>Защо е използвана резервната оценка?</h4><ul>{evidence.fallback_reasons.map((reason, index) => <li key={index}>{FALLBACK_LABELS[reason] ?? reason}</li>)}</ul></>}
        <p className="management-muted">Метод: {session.method_id} · Версия на оценката: {evidence.model_version}</p>
      </>}
      <h4>Готовност и натрупано натоварване</h4>
      <p className="management-muted">Готовността след задачата е прогноза. Липсващата оценка е показана с „—“. 90% готовност не означава 90% от капацитета.</p>
      <div className="management-table-wrap"><table><thead><tr><th>Компонент</th><th>Преди</th><th>След</th><th>7/40</th><th>Цел за 7 дни*</th><th>Натрупано*</th><th>Дефицит*</th></tr></thead><tbody>{COMPONENTS.map(zone => {
        const budget = day.load_budget.components[zone];
        return <tr key={zone}><th>{zone}</th><td>{number(day.readiness_before[zone])}{day.readiness_before[zone] === null ? "" : "%"}</td><td>{number(day.readiness_after[zone])}{day.readiness_after[zone] === null ? "" : "%"}</td><td>{number(budget?.index_7_40, 2)}</td><td>{number(budget?.target_weekly_effective)}</td><td>{number(budget?.rolling_7d_effective)}</td><td>{number(budget?.deficit_effective)}</td></tr>;
      })}</tbody></table></div><p className="management-muted">* Приравнени минути по компонент; те се различават от продължителността на сесията и измереното време в зона.</p>
      {day.rejected_alternatives.length > 0 && <><h4>Защо не е избрана друга задача?</h4><ul>{day.rejected_alternatives.map((alternative, index) => <li key={index}>{alternative.reason} <small>({alternative.method_id})</small></li>)}</ul></>}
    </details>
  </article>;
}

function DayCard({ day, expanded = false }: { day: DraftDay; expanded?: boolean }) {
  const sessions = daySessions(day);
  if (sessions.length < 2) return <SingleSessionCard day={{...day, session: sessions[0] ?? null}} expanded={expanded}/>;
  return <section aria-label="Тренировките за деня">{sessions.map((session, index) =>
    <SingleSessionCard key={index} day={{...day, session, explanation: "Сесия "+(index+1)+" от "+sessions.length+". "+day.explanation}} expanded={expanded}/>)}</section>;
}

async function requestJson(path: string, method: "GET" | "PUT" | "POST", body?: unknown, signal?: AbortSignal) {
  const response = await fetch(`/api/athlete/management/${path}`, { method, signal, cache: "no-store", headers: { "Content-Type": "application/json" }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  const value: unknown = await response.json();
  if (!response.ok) throw new Error(isRecord(value) && typeof value.error === "string" ? value.error : "Неуспешна заявка.");
  return value;
}

export function TrainingManagement({ initialView = "week", initialOutlook = null, athleteName, canEdit, initialProfile, initialDrafts, initialActive = { active: null, history: [] }, initialSyncState = null, syncError = false, today }: {
  initialOutlook?: ManagementOutlook | null; initialView?: "week" | "overview"; athleteName: string; canEdit: boolean; initialProfile: ManagementProfileResponse; initialDrafts: DraftRecord[]; initialActive?: ActivePlanResponse; initialSyncState?: SyncState | null; syncError?: boolean; today: string;
}) {
  const [active, setActive] = useState(initialActive);
  const saved = initialProfile;
  const [drafts, setDrafts] = useState(initialDrafts);
  const [startDate, setStartDate] = useState(() => { const earliest = saved.profile && saved.profile.program_start > today ? saved.profile.program_start : today; const latest = saved.profile?.horizon_mode === "MANUAL" && saved.profile.program_end < datePlus(today, 7) ? saved.profile.program_end : datePlus(today, 7); const previous = initialDrafts[0]?.payload.start_date; return previous && previous >= earliest && previous <= latest && previous <= datePlus(today, 1) ? previous : earliest > datePlus(today, 1) ? earliest : latest < datePlus(today, 1) ? latest : datePlus(today, 1); });
  const [busy, setBusy] = useState<"generate" | "activate" | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const activeRequest = useRef(false);
  const receiveActive = useCallback((value: ActivePlanResponse) => setActive(current => newerActivePlan(current, value)), []);
  const beginActiveChange = useCallback(() => {
    if (activeRequest.current) return false;
    activeRequest.current = true; setBusy("generate"); setNotice(""); setError(""); return true;
  }, []);
  const endActiveChange = useCallback(() => { activeRequest.current = false; setBusy(null); }, []);

  const draft = drafts[0];
  const guidance = saved.profile?.horizon_mode === "MANUAL" && saved.profile.program_end < today ? { step: "PROFILE", title: "Периодът на подготовката е приключил", description: "Задай следващия период в профила, за да подготвим нова програма." } : saved.profile && saved.profile.program_start > datePlus(today, 7) ? { step: "WAIT", title: "Подготовката започва по-късно", description: "Седмичната програма може да бъде подготвена до седем дни преди началото. При нужда промени датите в профила." } : managementGuidance({ configured: saved.configured, dirty: false, draft, sync: initialSyncState, today });
  const view = initialView;
  const [newDraft, setNewDraft] = useState(false);
  const showDraft = !active.active || newDraft;
  const overviewPlan = showDraft ? draft?.payload : (active.active?.payload.proposal ?? active.active?.payload.plan);
  const archivedDraft = showDraft && !!draft && draft.stale !== false;
  const sessionsCurrent = overviewPlan && (showDraft ? !archivedDraft : active.active?.stale === false)
    && (!initialOutlook?.engine_version || overviewPlan.engine_version === initialOutlook.engine_version);
  const sessionStatus = !sessionsCurrent ? "Няма актуални съставени сесии. Подготви или обнови седмичната програма."
    : showDraft ? "Времето е от проект за преглед; тренировките още не са утвърдени."
    : active.active?.payload.proposal ? "Времето е от предложена адаптация за преглед; тя още не е действаща програма."
    : active.active?.payload.status === "ACTIVE" ? "Времето е от текущата утвърдена програма."
    : "Времето е от запазена програма, която в момента не е активна.";

  useEffect(() => {
    if (initialView !== "week") return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    let request: AbortController | null = null;
    const refresh = async () => {
      try {
        if (document.visibilityState !== "visible" || activeRequest.current) return;
        request = new AbortController();
        const signal = AbortSignal.any([request.signal, AbortSignal.timeout(75_000)]);
        const latest = parseActivePlanResponse(await requestJson("active", "GET", undefined, signal));
        if (!cancelled && !activeRequest.current) receiveActive(latest);
      } catch { /* Keep the last known state; writes still enforce current revisions. */ }
      finally { if (!cancelled) timer = setTimeout(refresh, 60_000); }
    };
    timer = setTimeout(refresh, 60_000);
    return () => { cancelled = true; clearTimeout(timer); request?.abort(); };
  }, [initialView, receiveActive]);

  async function activateDraft() {
    if (!draft) return;
    setBusy("activate"); setError("");
    try {
      const result = await requestJson("activate", "POST", { start_date: draft.payload.start_date, draft_revision: draft.revision, expected_revision: active.active?.revision ?? 0 });
      receiveActive(parseActivePlanResponse(result)); setNewDraft(false); setNotice("Програмата е започната. Избери ден, за да видиш тренировката.");
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Програмата не беше утвърдена."); }
    finally { setBusy(null); }
  }

  const prepare = useCallback(async () => {
    setError(""); setNotice(""); setBusy("generate");
    try {
      const currentHistory = parseDrafts(await requestJson(`drafts?start_date=${encodeURIComponent(startDate)}`, "GET"));
      const existing = currentHistory.filter(item => item.payload.start_date === startDate).reduce((maximum, item) => Math.max(maximum, item.revision), 0);
      const generated = parseDraftRecord(await requestJson("generate", "POST", { start_date: startDate, expected_profile_revision: saved.revision, expected_draft_revision: existing }));
      const record = { ...generated, stale: false };
      setDrafts(previous => [record, ...previous.filter(item => !(item.entry_key === record.entry_key && item.revision === record.revision))]);
      setNotice(record.payload.status === "BLOCKED" ? "Нужна е още една стъпка. Виж указанието по-долу." : "Програмата е подготвена. Прегледай я преди започване.");
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Програмата не беше създадена."); }
    finally { setBusy(null); }
  }, [saved.revision, startDate]);

  async function generate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await prepare();
  }
  const refreshed = useRef(false);
  useEffect(() => {
    if (refreshed.current || initialView !== "week" || !canEdit || !saved.configured || busy) return;
    const current = active.active;
    if (current?.stale && !["PAUSED", "COMPLETED"].includes(current.payload.status)) {
      refreshed.current = true;
      void Promise.resolve().then(async () => {
        if (!beginActiveChange()) return;
        try {
          const result = await changeActivePlan("action", {action: "REFRESH", expected_revision: current.revision});
          receiveActive(result.value);
          setNotice(result.conflict ? PLAN_CHANGED_NOTICE : "Програмата е преизчислена. Прегледай предложението и ограниченията по-долу.");
        } catch (caught) { setError(caught instanceof Error ? caught.message : "Обновяването не завърши."); }
        finally { endActiveChange(); }
      });
    } else if (!current && draft?.stale === true && ["GENERATE", "START_DATE"].includes(guidance.step)) {
      refreshed.current = true;
      void Promise.resolve().then(prepare);
    }
  }, [active, busy, canEdit, draft, guidance.step, initialView, prepare, saved.configured, beginActiveChange, endActiveChange, receiveActive]);

  function exportDraft() {
    if (!draft) return;
    const blob = new Blob([JSON.stringify(draft, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob); const anchor = document.createElement("a");
    anchor.href = url; anchor.download = `onflows-plan-${draft.payload.start_date}-v${draft.revision}.json`; anchor.click(); URL.revokeObjectURL(url);
  }

  const generateForm = <form className="management-generate" onSubmit={generate}><fieldset disabled={!canEdit || busy !== null || !saved.configured}><label>Начало на седмицата<input aria-label="Начална дата на програмата" required type="date" min={saved.profile && saved.profile.program_start > today ? saved.profile.program_start : today} max={saved.profile?.horizon_mode === "MANUAL" && saved.profile.program_end < datePlus(today, 7) ? saved.profile.program_end : datePlus(today, 7)} value={startDate} onChange={event => setStartDate(event.target.value)} /></label><button className="action-button" type="submit">{busy === "generate" ? "Подготвям програмата…" : "Подготви програма"}</button></fieldset></form>;
  return <main className="activities-page management-page">
    <header className="activities-hero"><div className="activities-title"><div><p className="eyebrow">{athleteName}</p><h1>{view === "week" ? "Седмична програма" : "Дългосрочен план"}</h1><p>Подготовката в перспектива. Конкретната задача за всеки ден.</p></div></div><Link className="text-action" href="/planning">Профил за планиране →</Link></header>
    {!canEdit && <p className="management-notice">Имате достъп за преглед. Промените изискват право за редакция на плана.</p>}
    {error && <p className="management-error" role="alert">{error}</p>}{notice && <p className="management-notice" role="status">{notice}</p>}
    {syncError && <p className="management-error" role="alert">Обновяването не започна. Опитай отново или провери връзката с Intervals в настройките.</p>}
    {initialSyncState && <SyncStatusPanel initialState={initialSyncState} renderedGenerationId={initialSyncState.active_generation_id} returnTo="/management" compact />}
    <nav className="management-view-switch" aria-label="Изглед на плана"><Link href="/management" aria-current={view === "week" ? "page" : undefined}>Седмична програма</Link><Link href="/management/outlook" aria-current={view === "overview" ? "page" : undefined}>Дългосрочен план</Link></nav>
    {view === "week" && overviewPlan && !archivedDraft && (showDraft || active.active?.stale === false) && <TrainingPlanSummary plan={overviewPlan} />}

    {view === "overview" ? <TrainingPlanOverview plan={initialOutlook ?? undefined} outcomes={active.active?.payload.outcomes ?? []} today={today} currentProfileRevision={initialOutlook?.profile_revision} volumeContext={initialOutlook?.volume_context} sessions={sessionsCurrent ? overviewPlan : undefined} sessionStatus={sessionStatus} /> : <>
      {active.active && !newDraft && <ActiveTrainingPlan value={active} onChange={receiveActive} externalBusy={busy !== null} onBegin={beginActiveChange} onEnd={endActiveChange} canEdit={canEdit} today={today} renderDay={day => <DayCard day={day} expanded />} />}
      {showDraft && <>
        <section className="management-panel management-next-step" aria-label="Следваща стъпка"><p className="eyebrow">{guidance.step === "REVIEW" ? "Преглед преди започване" : "Следваща стъпка"}</p><h2>{guidance.title}</h2><p>{guidance.description}</p>
          {archivedDraft && <p className="management-notice">Показваният досега проект е остарял. Неговите часове и тренировки не отразяват текущите настройки. Новият проект се преизчислява с актуалните записани данни. Ако това не завърши, използвай „Подготви програма“.</p>}
          {guidance.step === "PROFILE" && <Link className="action-button" href="/planning#basic-profile">Попълни профила →</Link>}
          {guidance.step === "SYNC" && canEdit && <SyncActionForm scope="FULL" returnTo="/management" label="Обнови тренировките" />}
          {["GENERATE", "START_DATE"].includes(guidance.step) && generateForm}
          {guidance.step === "CHECK" && <div className="management-actions"><a href="#plan-details">Виж конкретните причини ↓</a><Link href="/planning">Провери профила →</Link></div>}
          {guidance.step === "REVIEW" && <><p className="management-muted">{saved.profile?.adaptation_mode === "AUTO" ? "След започване следващите дни ще се адаптират автоматично според реалното изпълнение." : "Промените ще се предлагат за твое утвърждаване."} Можеш да поставиш програмата на пауза.</p><button type="button" className="action-button" disabled={!canEdit || busy !== null || draft?.stale !== false || draft?.payload.activation_eligible !== true} onClick={activateDraft}>{busy === "activate" ? "Започване…" : "Започни програмата"}</button></>}
        </section>
        {draft && !archivedDraft && hasProgramDays(draft.payload) && <section className="management-plan" aria-label="Проект на тренировъчна програма"><h2>{dateLabel(draft.payload.start_date)} – {dateLabel(draft.payload.end_date)}</h2><p className="management-muted">Предложение за преглед — още не е започната програма.</p><TrainingPlanWeek days={draft.payload.days} today={today} renderDay={day => <DayCard day={day} expanded />} /></section>}
      </>}
    </>}
    {view === "week" && !archivedDraft && <PlanComparison plan={overviewPlan} outcomes={active.active?.payload.outcomes ?? []} />}
    {view === "week" && <details className="management-panel" id="plan-details"><summary>Обяснения и отчет</summary>
      {active.active && <button type="button" className="action-button secondary" onClick={() => { setNewDraft(!newDraft); }}>{newDraft ? "Обратно към активната програма" : "Подготви друга програма"}</button>}
      {draft && !archivedDraft && <>
        <details><summary>Всички условия и пояснения · {draft.payload.warnings.length}</summary><ul>{draft.payload.warnings.map((warning, index) => <li key={`${warning.code}-${index}`}>{warning.message}</li>)}</ul></details>
        {!hasProgramDays(draft.payload) && <details><summary>Оценка по дни</summary>{draft.payload.days.map(day => <DayCard key={day.date} day={day} />)}</details>}
        <details><summary>Периодизация и източници</summary><PeriodizationTable value={draft.payload.periodization} /><pre>{JSON.stringify({ periodization: draft.payload.periodization, parameters: draft.payload.parameters, source: draft.payload.source, catalog: draft.payload.catalog }, null, 2)}</pre></details>
        <button type="button" className="action-button secondary" onClick={exportDraft}>Изтегли пълния отчет</button></>}
      {guidance.step !== "GENERATE" && guidance.step !== "START_DATE" && saved.configured && <details><summary>Преизчисли за друга начална дата</summary>{generateForm}</details>}
      {canEdit && guidance.step !== "SYNC" && guidance.step !== "WAIT" && <SyncActionForm scope="FULL" returnTo="/management" label="Обнови тренировките от Intervals" />}
    </details>}
  </main>;
}
