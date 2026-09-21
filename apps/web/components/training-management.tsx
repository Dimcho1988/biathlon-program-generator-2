"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import { durationHms } from "../lib/duration-format";
import { isRecord } from "../lib/training-status";
import { ActiveTrainingPlan } from "./active-training-plan";
import { TrainingPlanWeek } from "./training-plan-week";
import { TrainingPlanOverview } from "./training-plan-overview";
import { managementGuidance, hasProgramDays } from "../lib/management-guidance";
import { SyncActionForm } from "./sync-action-form";
import { SyncStatusPanel } from "./sync-status-panel";
import type { SyncState } from "../lib/sync";
import {
  CAPACITY_LABELS, COMPONENTS, PHASE_LABELS, parseDraftRecord, parseDrafts,
  parseActivePlanResponse, type ActivePlanResponse, type DraftDay, type DraftRecord, type ManagementProfileResponse,
} from "../lib/training-management";

const number = (value: unknown, digits = 1) => typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("bg-BG", { maximumFractionDigits: digits }) : "—";
const dateLabel = (day: string) => new Date(`${day}T12:00:00Z`).toLocaleDateString("bg-BG", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });
const datePlus = (day: string, days: number) => { const value = new Date(`${day}T12:00:00Z`); value.setUTCDate(value.getUTCDate() + days); return value.toISOString().slice(0, 10); };
const STATUS_LABELS: Record<string, string> = { TRAINING: "Тренировка", REST: "Почивка", EXISTING_ACTIVITY: "Има изпълнена активност", UNAVAILABLE: "Няма свободно време", RACE: "Състезание", REVIEW_REQUIRED: "Нужен е преглед", SKIPPED: "Пропусната тренировка" };
const LIMIT_LABELS: Record<string, string> = { METHOD_WORK_CAP: "Максимална основна работа за метода", DAILY_AVAILABLE_WORK: "Оставащо време след загрявка и разпускане", REMAINING_WEEKLY_WORK: "Оставащ седмичен обем за основна работа", LOW_ABSOLUTE_RECOVERY_CAP: "Лимит на възстановителната работа", TAPER_DAILY_WORK_CAP: "Лимит за предсъстезателно разтоварване", ROLLING_7_40_COMPONENT_BUDGET: "Оставащ компонентен бюджет", RACE_DURATION_WORK_CAP: "Граница според дисциплината", RESERVE_KEY_SESSION_TIME: "Запазено време за ключовите сесии" };
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

function DayCard({ day, expanded = false }: { day: DraftDay; expanded?: boolean }) {
  const session = day.session;
  const evidence = session?.dose_evidence;
  return <article className={`management-day ${session ? "has-session" : ""}`}>
    <header><div><p className="management-day-date">{dateLabel(day.date)}</p><h3>{session?.title ?? STATUS_LABELS[day.status] ?? day.status}</h3></div>
      <span className="management-badge">{PHASE_LABELS[day.period] ?? day.period}{day.taper ? " · тейпър" : ""}</span></header>
    {session && <p className="management-session-total"><strong>{durationHms(session.total_minutes)}</strong> общо · {durationHms(session.main_work_minutes)} основна работа · {session.zone}</p>}
    <p>{day.explanation}</p>
    {session && <details className="management-execution" open={expanded}><summary>Как да изпълня тренировката</summary><ol className="management-blocks">{session.blocks.map((block, index) => <li key={`${block.kind}-${index}`}><div><strong>{block.label}</strong><span>{durationHms(block.duration_min)} · {block.zone}</span></div><p>{block.instructions}</p>
      {(block.target_hr_bpm !== null || block.target_speed_kmh !== null) && <small>{block.target_hr_bpm !== null ? `${number(block.target_hr_bpm, 0)} уд./мин` : ""}{block.target_hr_bpm !== null && block.target_speed_kmh !== null ? " · " : ""}{block.target_speed_kmh !== null ? `${number(block.target_speed_kmh)} km/h${block.primary_control === "EFFORT_AND_QUALITY" && block.speed_basis !== "FLAT_EQUIVALENT" ? " · зададена скорост" : " · равнинна референция, не темпо по наклон"}` : ""}</small>}
    </li>)}</ol></details>}
    <details className="management-detail"><summary>Защо тази задача и доза?</summary>
      {evidence && <><p><strong>Основа: {CAPACITY_LABELS[evidence.capacity_source] ?? evidence.capacity_source}.</strong></p><p>{evidence.explanation}</p>
        <dl className="management-facts"><div><dt>{evidence.capacity_source === "STRENGTH_METHOD_PROFILE" ? "Работна граница на силовия профил" : "Непрекъсната устойчивост"}</dt><dd>{durationHms(evidence.capacity_minutes)}</dd></div><div><dt>Дял според метода</dt><dd>{number(evidence.fraction * 100)}%</dd></div><div><dt>Първоначално поискана работа</dt><dd>{durationHms(evidence.requested_work_minutes)}</dd></div><div><dt>Предписана основна работа</dt><dd>{durationHms(evidence.prescribed_work_minutes)}</dd></div></dl>
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

async function requestJson(path: string, method: "GET" | "PUT" | "POST", body?: unknown) {
  const response = await fetch(`/api/athlete/management/${path}`, { method, cache: "no-store", headers: { "Content-Type": "application/json" }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  const value: unknown = await response.json();
  if (!response.ok) throw new Error(isRecord(value) && typeof value.error === "string" ? value.error : "Неуспешна заявка.");
  return value;
}

export function TrainingManagement({ athleteName, canEdit, initialProfile, initialDrafts, initialActive = { active: null, history: [] }, initialSyncState = null, syncError = false, today }: {
  athleteName: string; canEdit: boolean; initialProfile: ManagementProfileResponse; initialDrafts: DraftRecord[]; initialActive?: ActivePlanResponse; initialSyncState?: SyncState | null; syncError?: boolean; today: string;
}) {
  const [active, setActive] = useState(initialActive);
  const saved = initialProfile;
  const [drafts, setDrafts] = useState(initialDrafts);
  const [selected, setSelected] = useState(0);
  const [startDate, setStartDate] = useState(() => { const earliest = saved.profile && saved.profile.program_start > today ? saved.profile.program_start : today; const latest = saved.profile && saved.profile.program_end < datePlus(today, 7) ? saved.profile.program_end : datePlus(today, 7); return earliest > datePlus(today, 1) ? earliest : latest < datePlus(today, 1) ? latest : datePlus(today, 1); });
  const [busy, setBusy] = useState<"generate" | "activate" | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const draft = drafts[selected];
  const guidance = saved.profile && saved.profile.program_end < today ? { step: "PROFILE", title: "Периодът на подготовката е приключил", description: "Задай следващия период в профила, за да подготвим нова програма." } : saved.profile && saved.profile.program_start > datePlus(today, 7) ? { step: "WAIT", title: "Подготовката започва по-късно", description: "Седмичната програма може да бъде подготвена до седем дни преди началото. При нужда промени датите в профила." } : managementGuidance({ configured: saved.configured, dirty: false, draft, sync: initialSyncState, today });
  const [view, setView] = useState<"week" | "overview">("week");
  const [newDraft, setNewDraft] = useState(false);
  const showDraft = !active.active || newDraft;
  const overviewPlan = showDraft ? draft?.payload : (active.active?.payload.proposal ?? active.active?.payload.plan);

  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      if (document.visibilityState !== "visible") return;
      try { const latest = parseActivePlanResponse(await requestJson("active", "GET")); if (!cancelled) setActive(latest); } catch { /* Keep the last known state; writes still enforce current revisions. */ }
    };
    const timer = setInterval(refresh, 60_000);
    return () => { cancelled = true; clearInterval(timer); };
  }, []);

  async function activateDraft() {
    if (!draft) return;
    setBusy("activate"); setError("");
    try {
      const result = await requestJson("activate", "POST", { start_date: draft.payload.start_date, draft_revision: draft.revision, expected_revision: active.active?.revision ?? 0 });
      setActive(parseActivePlanResponse(result)); setNewDraft(false); setNotice("Програмата е започната. Избери ден, за да видиш тренировката.");
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Програмата не беше утвърдена."); }
    finally { setBusy(null); }
  }

  async function generate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(""); setNotice(""); setBusy("generate");
    try {
      const currentHistory = parseDrafts(await requestJson(`drafts?start_date=${encodeURIComponent(startDate)}`, "GET"));
      const existing = currentHistory.filter(item => item.payload.start_date === startDate).reduce((maximum, item) => Math.max(maximum, item.revision), 0);
      const generated = parseDraftRecord(await requestJson("generate", "POST", { start_date: startDate, expected_profile_revision: saved.revision, expected_draft_revision: existing }));
      const record = { ...generated, stale: false };
      setDrafts(previous => [record, ...previous.filter(item => !(item.entry_key === record.entry_key && item.revision === record.revision))]); setSelected(0);
      setNotice(record.payload.status === "BLOCKED" ? "Нужна е още една стъпка. Виж указанието по-долу." : "Програмата е подготвена. Прегледай я преди започване.");
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Програмата не беше създадена."); }
    finally { setBusy(null); }
  }

  function exportDraft() {
    if (!draft) return;
    const blob = new Blob([JSON.stringify(draft, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob); const anchor = document.createElement("a");
    anchor.href = url; anchor.download = `onflows-plan-${draft.payload.start_date}-v${draft.revision}.json`; anchor.click(); URL.revokeObjectURL(url);
  }

  const generateForm = <form className="management-generate" onSubmit={generate}><fieldset disabled={!canEdit || busy !== null || !saved.configured}><label>Начало на седмицата<input aria-label="Начална дата на програмата" required type="date" min={saved.profile && saved.profile.program_start > today ? saved.profile.program_start : today} max={saved.profile && saved.profile.program_end < datePlus(today, 7) ? saved.profile.program_end : datePlus(today, 7)} value={startDate} onChange={event => setStartDate(event.target.value)} /></label><button className="action-button" type="submit">{busy === "generate" ? "Подготвям програмата…" : "Подготви програма"}</button></fieldset></form>;
  return <main className="activities-page management-page">
    <header className="activities-hero"><div className="activities-title"><div><p className="eyebrow">{athleteName}</p><h1>Тренировъчен план</h1><p>Подготовката в перспектива. Конкретната задача за всеки ден.</p></div></div><Link className="text-action" href="/planning">Профил за планиране →</Link></header>
    {!canEdit && <p className="management-notice">Имате достъп за преглед. Промените изискват право за редакция на плана.</p>}
    {error && <p className="management-error" role="alert">{error}</p>}{notice && <p className="management-notice" role="status">{notice}</p>}
    {syncError && <p className="management-error" role="alert">Обновяването не започна. Опитай отново или провери връзката с Intervals в настройките.</p>}
    {initialSyncState && <SyncStatusPanel initialState={initialSyncState} renderedGenerationId={initialSyncState.active_generation_id} returnTo="/management" compact />}
    <nav className="management-view-switch" aria-label="Изглед на плана"><button type="button" aria-pressed={view === "week"} onClick={() => setView("week")}>Седмична програма</button><button type="button" aria-pressed={view === "overview"} onClick={() => setView("overview")}>Дългосрочна подготовка</button></nav>
    {view === "overview" ? <TrainingPlanOverview plan={overviewPlan} outcomes={active.active?.payload.outcomes ?? []} today={today} stale={showDraft ? draft?.stale !== false : active.active?.stale} /> : <>
      {active.active && !newDraft && <ActiveTrainingPlan value={active} onChange={setActive} canEdit={canEdit} today={today} renderDay={day => <DayCard day={day} expanded />} />}
      {showDraft && <>
        <section className="management-panel management-next-step" aria-label="Следваща стъпка"><p className="eyebrow">{guidance.step === "REVIEW" ? "Преглед преди започване" : "Следваща стъпка"}</p><h2>{guidance.title}</h2><p>{guidance.description}</p>
          {guidance.step === "PROFILE" && <Link className="action-button" href="/planning#basic-profile">Попълни профила →</Link>}
          {guidance.step === "SYNC" && canEdit && <SyncActionForm scope="FULL" returnTo="/management" label="Обнови тренировките" />}
          {["GENERATE", "START_DATE"].includes(guidance.step) && generateForm}
          {guidance.step === "CHECK" && <div className="management-actions"><a href="#plan-details">Виж конкретните причини ↓</a><Link href="/planning">Провери профила →</Link></div>}
          {guidance.step === "REVIEW" && <><p className="management-muted">{saved.profile?.adaptation_mode === "AUTO" ? "След започване следващите дни ще се адаптират автоматично според реалното изпълнение." : "Промените ще се предлагат за твое утвърждаване."} Можеш да поставиш програмата на пауза.</p><button type="button" className="action-button" disabled={!canEdit || busy !== null || draft?.stale !== false || draft?.payload.activation_eligible !== true} onClick={activateDraft}>{busy === "activate" ? "Започване…" : "Започни програмата"}</button></>}
        </section>
        {draft && hasProgramDays(draft.payload) && <section className="management-plan" aria-label="Проект на тренировъчна програма"><h2>{dateLabel(draft.payload.start_date)} – {dateLabel(draft.payload.end_date)}</h2><p className="management-muted">{draft.stale !== false ? "Предишна версия — само за справка. Входните данни са променени или актуалността им не е потвърдена." : "Предложение за преглед — още не е започната програма."}</p><TrainingPlanWeek days={draft.payload.days} today={today} renderDay={day => <DayCard day={day} expanded />} /></section>}
      </>}
    </>}
    <details className="management-panel" id="plan-details"><summary>Подробности и предишни програми</summary>
      {active.active && <button type="button" className="action-button secondary" onClick={() => { setNewDraft(!newDraft); setView("week"); }}>{newDraft ? "Обратно към активната програма" : "Подготви друга програма"}</button>}
      {draft && <><label>Запазени програми<select value={selected} onChange={event => { setSelected(Number(event.target.value)); setNewDraft(true); }}>{drafts.map((item, index) => <option key={`${item.entry_key}-${item.revision}`} value={index}>{item.payload.start_date} · версия {item.revision}</option>)}</select></label>
        {draft.stale_reason && <p>{draft.stale_reason}</p>}
        <details><summary>Всички условия и пояснения · {draft.payload.warnings.length}</summary><ul>{draft.payload.warnings.map((warning, index) => <li key={`${warning.code}-${index}`}>{warning.message}</li>)}</ul></details>
        {!hasProgramDays(draft.payload) && <details><summary>Оценка по дни</summary>{draft.payload.days.map(day => <DayCard key={day.date} day={day} />)}</details>}
        <details><summary>Периодизация и източници</summary><PeriodizationTable value={draft.payload.periodization} /><pre>{JSON.stringify({ periodization: draft.payload.periodization, parameters: draft.payload.parameters, source: draft.payload.source, catalog: draft.payload.catalog }, null, 2)}</pre></details>
        <button type="button" className="action-button secondary" onClick={exportDraft}>Изтегли пълния отчет</button></>}
      {guidance.step !== "GENERATE" && guidance.step !== "START_DATE" && saved.configured && <details><summary>Преизчисли за друга начална дата</summary>{generateForm}</details>}
      {canEdit && guidance.step !== "SYNC" && guidance.step !== "WAIT" && <SyncActionForm scope="FULL" returnTo="/management" label="Обнови тренировките от Intervals" />}
    </details>
  </main>;
}
