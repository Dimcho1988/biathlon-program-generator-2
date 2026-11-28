"use client";
import {fetchPrepared} from "../lib/prepared-view";
import { TrainingStructurePanel } from "./training-proposals";
import {ManagementPreparationNotice} from "./management-preparation-notice";
import { RunningSpeedUnitControl } from "./running-speed-unit";
import { useManagementFetch } from "./management-scope";
import { parsePlanningJob, planningPending, planningStageLabel, planningFailure, type PlanningJob } from '../lib/planning-job';

import Link from "next/link";
import { newerActivePlan } from "../lib/active-plan-request";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { AthleteWorkout } from "./athlete-workout";
import { isCalendarDate, isRecord } from "../lib/training-status";
import { ActiveTrainingPlan } from "./active-training-plan";
import { TrainingPlanWeek } from "./training-plan-week";
import { TrainingPlanReport } from "./training-plan-report";
import { TrainingPlanOverview } from "./training-plan-overview";
import { IndividualLearningPanel } from "./individual-learning-panel";
import { managementGuidance, hasProgramDays } from "../lib/management-guidance";
import { availableScheduleWindows, isAdaptivePlan, planRangeLabel, planWeekView, selectedScheduleWindow } from "../lib/plan-week-view";
import { SyncActionForm } from "./sync-action-form";
import { SyncStatusPanel } from "./sync-status-panel";
import type { SyncState } from "../lib/sync";
import type { ManagementOverviewActive, ManagementPreparation } from "../lib/management-overview";
import {
  daySessions, parseDrafts,
  parseActivePlanResponse, type ActivePlanResponse, type ManagementOutlook, type DraftDay, type DraftRecord, type ManagementProfileResponse,
} from "../lib/training-management";

const EMPTY_ACTIVE: ActivePlanResponse = {active: null, history: []};
const dateLabel = (day: string) => new Date(`${day}T12:00:00Z`).toLocaleDateString("bg-BG", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });
const datePlus = (day: string, days: number) => { const value = new Date(`${day}T12:00:00Z`); value.setUTCDate(value.getUTCDate() + days); return value.toISOString().slice(0, 10); };
class ManagementRequestError extends Error {
  constructor(message: string, readonly status: number) { super(message); }
}
const STATUS_LABELS: Record<string, string> = { TRAINING: "Тренировка", REST: "Почивка", EXISTING_ACTIVITY: "Има изпълнена активност", UNAVAILABLE: "Няма свободно време", RACE: "Състезание", REVIEW_REQUIRED: "Нужен е преглед", SKIPPED: "Пропусната тренировка" };
function DayCard({ day }: { day: DraftDay }) {
  const sessions = daySessions(day);
  if (!sessions.length) return <article className="management-day"><p className="management-day-date">{dateLabel(day.date)}</p>
    <h3>{day.time_limit_exhausted ? "Изчерпан лимит за време" : STATUS_LABELS[day.status] ?? day.status}</h3><p>{day.explanation}</p></article>;
  return <section aria-label="Тренировките за деня">{sessions.map((session, index) =>
    <AthleteWorkout key={day.date + "-" + index} day={day} session={session}
      position={sessions.length > 1 ? "Тренировка " + (index + 1) + " от " + sessions.length : undefined}/>)}</section>;
}

export function TrainingManagement({ initialView = "week", initialOutlook = null, athleteName, canEdit, initialProfile, initialDrafts, initialActive = EMPTY_ACTIVE, initialOverviewActive, initialSyncState = null, renderedGenerationId, syncUnavailable = false, syncError = false, preparation = null, today: initialToday }: {
  preparation?: ManagementPreparation | null; initialOutlook?: ManagementOutlook | null; initialOverviewActive?: ManagementOverviewActive; initialView?: "week" | "overview"; athleteName: string; canEdit: boolean; initialProfile: ManagementProfileResponse; initialDrafts: DraftRecord[]; initialActive?: ActivePlanResponse; initialSyncState?: SyncState | null; renderedGenerationId?: string | null; syncUnavailable?: boolean; syncError?: boolean; today: string;
}) {
  const [today, setToday] = useState(initialToday);
  const scopedFetch = useManagementFetch();
  const requestJson = useCallback(async (path: string, method: "GET" | "PUT" | "POST", body?: unknown, signal?: AbortSignal) => {
  const response = await fetchPrepared(`/api/athlete/management/${path}`, { method, signal, cache: "no-store", headers: { "Content-Type": "application/json" }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) }, scopedFetch);
  const value: unknown = await response.json();
  if (!response.ok) throw new ManagementRequestError(isRecord(value) && typeof value.error === "string" ? value.error : "Неуспешна заявка.", response.status);
  return value;
}, [scopedFetch]);

  const [active, setActive] = useState(initialActive);
  const [serverActive, setServerActive] = useState(initialActive);
  if (serverActive !== initialActive) {
    setServerActive(initialActive);
    setActive(current => newerActivePlan(current, initialActive));
    if (isCalendarDate(initialActive.today)) setToday(initialActive.today);
  }
  const automaticPublication = active.capabilities?.automatic_publication === true;
  const saved = initialProfile;
  const [drafts, setDrafts] = useState(initialDrafts);
  const snapshot = isRecord(initialOutlook?.input_snapshot) ? initialOutlook.input_snapshot : {};
  const horizon = isRecord(snapshot.horizon) ? snapshot.horizon : {};
  const effectiveEnd = saved.profile?.horizon_mode === "MANUAL" ? saved.profile.program_end
    : isCalendarDate(horizon.effective_end) ? horizon.effective_end : undefined;
  const earliestStart = saved.profile && saved.profile.program_start > today ? saved.profile.program_start : today;
  const latestStart = effectiveEnd && effectiveEnd < datePlus(today, 7) ? effectiveEnd : datePlus(today, 7);
  const [startDate, setStartDate] = useState(() => { const earliest = earliestStart; const latest = latestStart; const previous = initialDrafts[0]?.payload.start_date; return previous && previous >= earliest && previous <= latest && previous <= datePlus(today, 1) ? previous : earliest > datePlus(today, 1) ? earliest : latest < datePlus(today, 1) ? latest : datePlus(today, 1); });
  const [busy, setBusy] = useState<"generate" | "activate" | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [planningJob, setPlanningJob] = useState<PlanningJob | null>(null);
  const [awaitingPublication, setAwaitingPublication] = useState(false);
  const activeRequest = useRef(false);
  const receiveActive = useCallback((value: ActivePlanResponse) => {
    if (isCalendarDate(value.today)) setToday(value.today);
    setActive(current => newerActivePlan(current, value));
  }, []);
  const beginActiveChange = useCallback(() => {
    if (activeRequest.current) return false;
    activeRequest.current = true; setBusy("generate"); setNotice(""); setError(""); return true;
  }, []);
  const endActiveChange = useCallback(() => { activeRequest.current = false; setBusy(null); }, []);

  const draft = drafts[0];
  const guidance = effectiveEnd && effectiveEnd < today ? { step: "PROFILE", title: "Периодът на подготовката е приключил", description: "Задай следващия период в профила, за да подготвим нова програма." } : saved.profile && saved.profile.program_start > datePlus(today, 7) ? { step: "WAIT", title: "Подготовката започва по-късно", description: "Седмичната програма може да бъде подготвена до седем дни преди началото. При нужда промени датите в профила." } : managementGuidance({ configured: saved.configured, dirty: false, draft, sync: initialSyncState, today, automaticPublication });
  const view = initialView;
  const [newDraft, setNewDraft] = useState(false);
  const [selectedWeek, setSelectedWeek] = useState<string>();
  const [showProposal, setShowProposal] = useState(false);
  const showDraft = !active.active || newDraft;
  const approvedPlan = active.active?.payload.plan;
  const overviewPlan = showDraft ? draft?.payload : approvedPlan && active.active?.payload.monitoring
    ? {...approvedPlan, history_comparison: active.active.payload.monitoring.history_comparison} : approvedPlan;
  const viewingProposal = !showDraft && showProposal && !!active.active?.payload.proposal;
  const visibleWeekPlan = viewingProposal ? active.active!.payload.proposal! : overviewPlan;
  const archivedDraft = showDraft && !!draft && draft.stale !== false;
  const draftWeek = draft ? selectedScheduleWindow(draft.payload,today,selectedWeek) : undefined;
  const draftSchedule = draft && draftWeek ? planWeekView(draft.payload,draftWeek) : undefined;
  const sessionsCurrent = overviewPlan && (showDraft ? !archivedDraft : active.active?.stale === false)
    && (!initialOutlook?.engine_version || overviewPlan.engine_version === initialOutlook.engine_version);
  const compactOverview = view === "overview" ? initialOverviewActive : undefined;
  const overviewSessions = compactOverview ?? overviewPlan;
  const overviewSessionsCurrent = compactOverview
    ? compactOverview.revision !== null && !compactOverview.stale
      && (!initialOutlook?.engine_version || compactOverview.engine_version === initialOutlook.engine_version)
    : sessionsCurrent;
  const sessionStatus = !overviewSessionsCurrent ? "Няма актуални съставени сесии. Подготви или обнови седмичната програма."
    : !compactOverview && showDraft ? "Времето е от подготвения вариант; той още не е приложен."
    : (compactOverview ? compactOverview.has_proposal : active.active?.payload.proposal) ? "Времето е от текущия график. Има отделен вариант, който още не е приложен."
    : (compactOverview?.status ?? active.active?.payload.status) === "ACTIVE" ? "Времето е от текущата програма."
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
        const state = await requestJson("state", "GET", undefined, signal);
        if (isRecord(state) && isCalendarDate(state.today) && state.today !== today) setToday(state.today);
        if (isRecord(state) && (!isCalendarDate(state.today) || state.today === today) && state.revision === (active.active?.revision ?? 0) && state.stale === (active.active?.stale ?? false)
          && JSON.stringify(state.background_update ?? null) === JSON.stringify(active.background_update ?? null)) return;
        const latest = parseActivePlanResponse(await requestJson("active", "GET", undefined, signal));
        if (!cancelled && !activeRequest.current) receiveActive(latest);
      } catch { /* Keep the last known state; writes still enforce current revisions. */ }
      finally { if (!cancelled) timer = setTimeout(refresh, active.active?.stale || ["QUEUED","RUNNING"].includes(active.background_update?.status ?? "") ? 5000 : 60_000); }
    };
    timer = setTimeout(refresh, active.active?.stale || ["QUEUED","RUNNING"].includes(active.background_update?.status ?? "") ? 5000 : 60_000);
    return () => { cancelled = true; clearTimeout(timer); request?.abort(); };
  }, [initialView, receiveActive, active.active?.revision, active.active?.stale, active.background_update, today, requestJson]);

  async function activateDraft() {
    if (!draft) return;
    setBusy("activate"); setError("");
    try {
      const result = await requestJson("activate", "POST", { start_date: draft.payload.start_date, draft_revision: draft.revision, expected_revision: active.active?.revision ?? 0 });
      receiveActive(parseActivePlanResponse(result)); setNewDraft(false); setNotice("Програмата е започната. Избери ден, за да видиш тренировката.");
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Програмата не беше утвърдена."); }
    finally { setBusy(null); }
  }

  const receiveGeneratedDraft = useCallback(async (job: PlanningJob, signal?: AbortSignal) => {
    if (job.kind === 'DAY') {
      const latest = parseActivePlanResponse(await requestJson("active", "GET", undefined, signal));
      receiveActive(latest);
      if (!latest.active || latest.active.revision < (job.result_active_revision ?? Infinity)) {
        setAwaitingPublication(true);
        setPlanningJob(job);
        return false;
      }
      setNewDraft(false);
      setAwaitingPublication(false);
      setNotice(latest.active.payload.status === "REVIEW_REQUIRED"
        ? "Изборът за деня е записан. Предложеният график изисква преглед."
        : "Изборът за деня е записан. Показана е актуалната програма.");
      return true;
    }
    let paused = false;
    if (automaticPublication) {
      const latest = parseActivePlanResponse(await requestJson("active", "GET", undefined, signal));
      receiveActive(latest);
      paused = latest.active?.payload.status === "PAUSED";
      const matchesOrigin = latest.active?.payload.origin?.start_date === job.start_date
        && latest.active?.payload.origin?.draft_revision === job.result_revision;
      const matchesReceipt = typeof job.result_active_revision === "number"
        && (latest.active?.revision ?? 0) >= job.result_active_revision;
      if (latest.active && (matchesReceipt || matchesOrigin && latest.active.stale === false)) {
        setNewDraft(false); setStartDate(job.start_date);
        setAwaitingPublication(false);
        setNotice(latest.active.payload.status === "PAUSED" ? "Програмата е записана и в момента е на пауза."
          : latest.active.payload.status === "COMPLETED" ? "Програмата е записана. Показаният период вече е завършен."
          : latest.active.stale ? "Програмата е записана. Показана е последната версия; новите данни още се обработват."
          : latest.active.payload.status !== "ACTIVE" ? "Програмата е записана. Последната проверка изисква уточняване на данните."
          : "Програмата е готова и е приложена. Избери ден, за да видиш тренировката.");
        return true;
      }
      if (typeof job.result_active_revision === "number") {
        setAwaitingPublication(true); setBusy("generate"); setPlanningJob(job);
        setNotice("Програмата е записана. Зареждаме актуалния график…");
        return false;
      }
    }
    const history = parseDrafts(await requestJson(`drafts?start_date=${encodeURIComponent(job.start_date)}&revision=${job.result_revision}`, 'GET', undefined, signal));
    const record = history.find(row => row.revision === job.result_revision && row.entry_key === job.start_date);
    if (!record) throw new ManagementRequestError('Готовата програма вече е променена. Презареди последната версия.', 404);
    setDrafts(previous => [record, ...previous.filter(item => !(item.entry_key === record.entry_key && item.revision === record.revision))]);
    setNewDraft(!paused);
    setStartDate(job.start_date);
    setNotice(paused ? 'Вариантът е запазен. Програмата е на пауза; избери „Продължи с текущите настройки“, когато си готов.'
      : record.payload.status === 'BLOCKED' || automaticPublication ? 'Програмата още не е приложена. Виж конкретната причина по-долу.' : 'Програмата е подготвена. Прегледай я преди започване.');
    setAwaitingPublication(false);
    return true;
  }, [requestJson, receiveActive, automaticPublication]);

  useEffect(() => {
    if (!canEdit || initialView !== 'week') return;
    let cancelled = false;
    const controller = new AbortController();
    void requestJson('generation-jobs', 'GET', undefined, controller.signal).then(async value => {
      const job = parsePlanningJob(value);
      if (cancelled) return;
      if (planningPending(job)) { setPlanningJob(job); setBusy('generate'); }
      else if (job?.status === 'SUCCEEDED' && (!initialActive.active || job.kind === 'DAY' && (job.result_active_revision ?? 0) > initialActive.active.revision)) {
        await receiveGeneratedDraft(job, controller.signal);
        if (!cancelled) setPlanningJob(job);
      } else if (job?.status === 'FAILED') {
        setPlanningJob(job); setError(planningFailure(job));
        return;
      }
    }).catch(() => { /* Unknown saved state must not trigger automatic preparation. */ });
    return () => { cancelled = true; controller.abort(); };
  }, [canEdit, initialView, initialActive.active, requestJson, receiveGeneratedDraft]);

  const pendingGeneration = planningPending(planningJob) || awaitingPublication;
  const pendingJobId = pendingGeneration ? planningJob?.job_id : null;
  useEffect(() => {
    if (!pendingJobId) return;
    const jobId = pendingJobId;
    let cancelled = false, polling = false, delay = 5000;
    let timer: ReturnType<typeof setTimeout>;
    let controller: AbortController | null = null;
    const poll = async () => {
      if (cancelled || polling) return;
      if (document.visibilityState === 'hidden') { timer = setTimeout(poll, 15_000); return; }
      polling = true;
      let again = true;
      try {
        controller = new AbortController();
        const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(20_000)]);
        const job = parsePlanningJob(await requestJson(`generation-jobs?job_id=${jobId}`, 'GET', undefined, signal));
        if (cancelled) return;
        if (!job) throw new Error('Заявката за подготовка не е намерена.');
        if (job.status === 'SUCCEEDED') {
          const received = await receiveGeneratedDraft(job, signal);
          if (!cancelled) { if (received) setBusy(null); setPlanningJob(job); }
          again = !received;
        } else if (job.status === 'FAILED') {
          setPlanningJob(job); setAwaitingPublication(false); setBusy(null); setError(planningFailure(job)); again = false;
        } else { setPlanningJob(job); setNotice(''); }
      } catch (caught) {
        if (!cancelled) {
          if (caught instanceof ManagementRequestError && [401, 403, 404, 409, 412, 422].includes(caught.status)) {
            setPlanningJob(null); setAwaitingPublication(false); setBusy(null); setError(caught.message); again = false;
          } else setNotice('Връзката с подготовката временно е прекъсната. Запазената заявка продължава във фонов режим.');
        }
      } finally {
        polling = false;
        if (!cancelled && again) { delay = Math.min(15_000, delay+2500); timer = setTimeout(poll, delay); }
      }
    };
    const visible = () => { if (document.visibilityState === 'visible') { clearTimeout(timer); void poll(); } };
    timer = setTimeout(poll, delay);
    document.addEventListener('visibilitychange', visible);
    return () => { cancelled = true; clearTimeout(timer); controller?.abort(); document.removeEventListener('visibilitychange', visible); };
  }, [pendingJobId, requestJson, receiveGeneratedDraft]);

  const prepare = useCallback(async () => {
    setError(""); setNotice(""); setBusy("generate");
    let queued = false;
    try {
      const metadata = await requestJson(`draft-metadata?start_date=${encodeURIComponent(startDate)}`, 'GET');
      if (!isRecord(metadata) || !Number.isSafeInteger(metadata.revision) || Number(metadata.revision)<0) throw new Error('Версията на програмата не е потвърдена. Презареди страницата.');
      const job = parsePlanningJob(await requestJson('generation-jobs', 'POST', {start_date: startDate,
        expected_profile_revision: saved.revision, expected_draft_revision: metadata.revision}));
      if (!job) throw new Error('Заявката за подготовка не е потвърдена.');
      if (planningPending(job)) { queued = true; setPlanningJob(job); }
      else if (job.status === 'SUCCEEDED') queued = !await receiveGeneratedDraft(job);
      else throw new Error(planningFailure(job));
    } catch (caught) {
      // An uncertain POST may already have committed. Read its durable state;
      // never automatically send a second generation request.
      try {
        const job = parsePlanningJob(await requestJson('generation-jobs', 'GET'));
        if (planningPending(job)) { queued = true; setPlanningJob(job); }
      } catch { /* The user can resume the saved request by reopening the page. */ }
      if (!queued) setError(caught instanceof Error ? caught.message : 'Заявката не е потвърдена. Презареди страницата.');
    }
    finally { if (!queued) setBusy(null); }
  }, [saved.revision, startDate, requestJson, receiveGeneratedDraft]);

  async function generate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await prepare();
  }

  function exportDraft() {
    if (!draft) return;
    const blob = new Blob([JSON.stringify(draft, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob); const anchor = document.createElement("a");
    anchor.href = url; anchor.download = `onflows-plan-${draft.payload.start_date}-v${draft.revision}.json`; anchor.click(); URL.revokeObjectURL(url);
  }

  const generateForm = <form className="management-generate" onSubmit={generate}><fieldset disabled={!canEdit || busy !== null || pendingGeneration || !saved.configured}><label>Начало на седмицата<input aria-label="Начална дата на програмата" required type="date" min={earliestStart} max={latestStart} value={startDate} onChange={event => setStartDate(event.target.value)} /></label><button className="action-button" type="submit">{busy === "generate" ? "Подготвям програмата…" : "Подготви програма"}</button></fieldset></form>;
  return <main className="activities-page management-page">
    <RunningSpeedUnitControl/>
    <header className="activities-hero"><div className="activities-title"><div><p className="eyebrow">{athleteName}</p><h1>{view === "week" ? "Седмична програма" : "Дългосрочен план"}</h1><p>Подготовката в перспектива. Конкретната задача за всеки ден.</p></div></div><Link className="text-action" href="/planning">Профил за планиране →</Link></header>
    {!canEdit && <p className="management-notice">Имате достъп за преглед. Промените изискват право за редакция на плана.</p>}
    <ManagementPreparationNotice preparation={preparation} timezone={saved.timezone} view={initialView}/>
    {error && <p className="management-error" role="alert">{error}</p>}{notice && <p className="management-notice" role="status">{notice}</p>}
    {pendingGeneration && planningJob && <section className="management-notice" aria-label="Подготовка на програмата">
      <p role="status">{planningStageLabel(planningJob)}</p>
      <p>{planningJob.kind === "DAY" ? "Промяна за" : "Начало"} {dateLabel(planningJob.start_date)}. Можеш да отвориш друг раздел и да се върнеш по-късно. Заявката остава запазена.</p>
    </section>}
    {syncError && <p className="management-error" role="alert">Обновяването не започна. Опитай отново или провери връзката с Intervals в настройките.</p>}
    {syncUnavailable && <p className="management-muted" role="status">Статусът на синхронизацията временно не е достъпен. Запазеният изглед остава видим.</p>}
    {initialSyncState && <SyncStatusPanel initialState={initialSyncState} renderedGenerationId={renderedGenerationId === undefined ? initialSyncState.active_generation_id : renderedGenerationId} renderedGenerationKnown={renderedGenerationId !== null} deferViewRefresh={preparation?.status === "UPDATING"} returnTo="/management" compact timezone={saved.timezone} />}
    <nav className="management-view-switch" aria-label="Изглед на плана"><Link href="/management" aria-current={view === "week" ? "page" : undefined}>Седмична програма</Link><Link href="/management/outlook" aria-current={view === "overview" ? "page" : undefined}>Дългосрочен план</Link></nav>
    {view === "week" && visibleWeekPlan && isAdaptivePlan(visibleWeekPlan) && <div className="management-panel">
      <label>Период<select aria-label="Период на адаптивната програма" value={selectedScheduleWindow(visibleWeekPlan,today,selectedWeek).start} onChange={event => setSelectedWeek(event.target.value)}>
        {availableScheduleWindows(visibleWeekPlan,today).map((window,index) => <option key={window.start} value={window.start}>{index === 0 ? "Следващите 7 дни" : "Следващата седмица"} · {planRangeLabel(window)}</option>)}
      </select></label>
    </div>}
    {view === "week" && saved.configured && <TrainingStructurePanel key={selectedWeek || today} today={today} defaultStart={visibleWeekPlan ? selectedScheduleWindow(visibleWeekPlan, today, selectedWeek).start : today} canEdit={canEdit} disabled={busy !== null || pendingGeneration}/>}
    {view === "week" && overviewPlan && <>
    {!showDraft && active.active?.stale && <p className="management-notice">Отчетът е от последната запазена програма. Новите данни още не са отразени в дозите.</p>}
    {view === "week" && overviewPlan && !archivedDraft && <TrainingPlanReport automaticPublication={automaticPublication} plan={overviewPlan} planKind={showDraft ? "DRAFT" : "APPROVED"} today={today} selectedWeek={isAdaptivePlan(overviewPlan) ? undefined : selectedWeek} onWeekChange={isAdaptivePlan(overviewPlan) ? undefined : setSelectedWeek} showProposal={!showDraft && showProposal} onShowProposalChange={setShowProposal} proposal={showDraft ? null : active.active?.payload.proposal} qExecution={showDraft ? undefined : active.active?.payload.q_execution} outcomes={showDraft ? [] : active.active?.payload.outcomes}/>}
    </>}
    {view === "overview" && <IndividualLearningPanel plan={initialOutlook} />}

    {view === "overview" ? <TrainingPlanOverview plan={initialOutlook ?? undefined} outcomes={compactOverview?.outcomes ?? active.active?.payload.outcomes ?? []} today={today} currentProfileRevision={initialOutlook?.profile_revision} volumeContext={initialOutlook?.volume_context} sessions={overviewSessionsCurrent ? overviewSessions : undefined} sessionStatus={sessionStatus} /> : <>
      {active.active && !newDraft && <ActiveTrainingPlan value={active} profile={saved} onChange={receiveActive} externalBusy={busy !== null || pendingGeneration} onDayJob={job => {
        setPlanningJob(job);
        setAwaitingPublication(job.status === "SUCCEEDED");
      }} onBegin={beginActiveChange} onEnd={endActiveChange} canEdit={canEdit} today={today} selectedWeek={selectedWeek} showProposal={showProposal} renderDay={day => <DayCard day={day} />} />}
      {showDraft && <>
        <section className="management-panel management-next-step" aria-label="Следваща стъпка"><p className="eyebrow">{guidance.step === "REVIEW" ? "Преглед преди започване" : "Следваща стъпка"}</p><h2>{guidance.title}</h2><p>{guidance.description}</p>
          {archivedDraft && <p className="management-notice">{draft?.stale_reason ?? "Показваният досега проект е остарял."} Избери „Подготви програма“, за да получиш проект с актуалните данни.</p>}
          {guidance.step === "PROFILE" && <Link className="action-button" href="/planning#basic-profile">Попълни профила →</Link>}
          {guidance.step === "SYNC" && canEdit && <SyncActionForm scope="FULL" returnTo="/management" label="Обнови тренировките" />}
          {["GENERATE", "START_DATE"].includes(guidance.step) && generateForm}
          {guidance.step === "CHECK" && <div className="management-actions"><a href="#plan-details">Виж конкретните причини ↓</a><Link href="/planning">Провери профила →</Link></div>}
          {guidance.step === "REVIEW" && <><p className="management-muted">{saved.profile?.adaptation_mode === "AUTO" ? "След започване следващите дни ще се адаптират автоматично според реалното изпълнение." : "Седмицата ще остане фиксирана до показания край. Промените и следващата седмица ще изискват утвърждаване."} Можеш да поставиш програмата на пауза.</p><button type="button" className="action-button" disabled={!canEdit || busy !== null || draft?.stale !== false || draft?.payload.activation_eligible !== true} onClick={activateDraft}>{busy === "activate" ? "Започване…" : "Започни програмата"}</button></>}
        </section>
        {draft && !archivedDraft && draftWeek && draftSchedule && hasProgramDays(draft.payload) && <section className="management-plan" aria-label="Проект на тренировъчна програма"><h2>{planRangeLabel(draftWeek)}</h2><p className="management-muted">{automaticPublication ? "Подготвен вариант — още не е приложен. Причината и следващата стъпка са показани по-горе." : "Предложение за преглед — още не е започната програма."}</p><TrainingPlanWeek days={draftSchedule.days} period={draftWeek} forecastDates={draftSchedule.forecastDates} programmeLabel="Предложение" today={today} renderDay={day => <DayCard day={day} />} /></section>}
      </>}
    </>}
    {view === "week" && <details className="management-panel" id="plan-details"><summary>Още действия и условия</summary>
      {active.active && <button type="button" className="action-button secondary" onClick={() => { setNewDraft(!newDraft); }}>{newDraft ? "Обратно към активната програма" : "Подготви друга програма"}</button>}
      {draft && !archivedDraft && <>
        <details><summary>Всички условия и пояснения · {draft.payload.warnings.length}</summary><ul>{draft.payload.warnings.map((warning, index) => <li key={`${warning.code}-${index}`}>{warning.message}</li>)}</ul></details>
        {!hasProgramDays(draft.payload) && <details><summary>Оценка по дни</summary>{draft.payload.days.map(day => <DayCard key={day.date} day={day} />)}</details>}
        <button type="button" className="action-button secondary" onClick={exportDraft}>Изтегли пълния отчет</button></>}
      {guidance.step !== "GENERATE" && guidance.step !== "START_DATE" && saved.configured && <details><summary>Преизчисли за друга начална дата</summary>{generateForm}</details>}
      {canEdit && guidance.step !== "SYNC" && guidance.step !== "WAIT" && <SyncActionForm scope="FULL" returnTo="/management" label="Обнови тренировките от Intervals" />}
    </details>}
  </main>;
}
