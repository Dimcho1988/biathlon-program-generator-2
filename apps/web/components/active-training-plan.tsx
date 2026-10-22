"use client";
import { useManagementFetch } from "./management-scope";

import { SymptomNotice } from "./symptom-notice";
import Link from "next/link";
import { TrainingPlanWeek } from "./training-plan-week";
import { PlanProposalReview } from "./plan-proposal-review";
import { hasProgramDays } from "../lib/management-guidance";
import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { isRecord } from "../lib/training-status";
import { durationHms } from "../lib/duration-format";
import { COMPONENTS, type ActivePlanResponse, type DraftDay } from "../lib/training-management";
import { changeActivePlan, PLAN_CHANGED_NOTICE } from "../lib/active-plan-request";
import { PlanCheckHistory } from "./plan-check-history";

const STATE: Record<string, string> = { ACTIVE: "Активна програма", PAUSED: "Програмата е на пауза", REVIEW_REQUIRED: "Промяна за преглед", COMPLETED: "Завършена програма" };
const OUTCOME: Record<string, string> = { RECORDED: "Има реално изпълнение", DIFFERENT_ACTIVITY: "Изпълнено е друго средство", MISSED: "Без изпълнена сесия", SKIPPED: "Пропусната", REST: "Почивка", UNKNOWN: "Липсват данни" };

export function ActiveTrainingPlan({ value, onChange, canEdit, today, renderDay, externalBusy = false, onBegin, onEnd }: {
  value: ActivePlanResponse; onChange: (v: ActivePlanResponse) => void; canEdit: boolean; today: string;
  externalBusy?: boolean; onBegin?: () => boolean; onEnd?: () => void;
  renderDay: (day: DraftDay) => ReactNode;
}) {
  const scopedFetch = useManagementFetch();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ revision: number; message: string } | null>(null);
  const [notice, setNotice] = useState("");
  const [checksRefreshKey, setChecksRefreshKey] = useState(0);
  const lastCheckId = useRef<string | null>(null);
  const latestRevision = useRef(value.active?.revision ?? 0);
  useLayoutEffect(() => { latestRevision.current = value.active?.revision ?? 0; }, [value.active?.revision]);
  const inFlight = useRef(false);
  const record = value.active;
  if (!record) return null;
  const p = record.payload;
  const display = p.plan;
  const checked = p.proposal ?? p.plan;
  const week = p.week_window ?? { start_date: display.start_date, end_date: display.end_date };
  const fixed = p.mode === "REVIEW";
  const progression = isRecord(checked.parameters?.load_progression) ? checked.parameters?.load_progression : {};
  const feedback = isRecord(progression.adaptation) ? progression.adaptation : {};
  const symptomHold = checked.warnings.some(w => w.code === "REPORTED_ILLNESS_OR_PAIN");
  async function mutate(endpoint: string, body: Record<string, unknown>) {
    if (!canEdit || inFlight.current || externalBusy || (onBegin && !onBegin())) return;
    inFlight.current = true;
    setBusy(true); setError(null); setNotice("");
    try {
      const recordCheck = endpoint === "action" && body.action === "REFRESH" && value.capabilities?.unchanged_checks === true;
      const result = await changeActivePlan(endpoint, { ...body, expected_revision: record!.revision,
        ...(recordCheck ? { record_unchanged_check: true } : {}) }, scopedFetch);
      const { action_result: receipt, ...current } = result.value;
      onChange(current);
      if (result.conflict) setNotice(PLAN_CHANGED_NOTICE);
      else if (endpoint === "action" && body.action === "APPROVE") setNotice("Предложението е утвърдено. Графикът по-долу съдържа приетите промени.");
      else if (recordCheck && receipt) {
        const displayedRevision = Math.max(latestRevision.current, current.active?.revision ?? 0);
        setNotice(receipt.checked_revision === displayedRevision
          ? "Проверката приключи. Програмата остава без промяна."
          : `Проверката на версия ${receipt.checked_revision} приключи без промяна. Показана е текущата версия ${displayedRevision}.`);
        if (lastCheckId.current !== receipt.check_id) {
          lastCheckId.current = receipt.check_id;
          setChecksRefreshKey(key => key + 1);
        }
      }
    } catch (caught) { setError({ revision: record!.revision, message: caught instanceof Error ? caught.message : "Програмата не беше променена." }); }
    finally { inFlight.current = false; setBusy(false); onEnd?.(); }
  }
  const actionsDisabled = !canEdit || busy || externalBusy;
  return <section className="management-active" aria-label="Активна тренировъчна програма">
    <div className="management-panel management-current-heading"><div><p className="eyebrow">Текущ план · версия {record.revision}</p><h2>{STATE[p.status]}</h2>
      <p>{p.mode === "AUTO" ? "Адаптивна седмица — оставащите задачи се адаптират автоматично след нов анализ и при смяна на деня." : "Фиксирана седмица — датите, методите и дозите се запазват до утвърждаване на промяна."}</p>{!symptomHold && !p.proposal && <p>{p.reason}</p>}
      <p>Утвърден период: {week.start_date} – {week.end_date}. <Link href="/planning">Смени режима в профила</Link></p>
      {p.proposal && p.proposal_mode && p.proposal_mode !== p.mode && <p role="status">Предложена смяна към {p.proposal_mode === "AUTO" ? "адаптивна" : "фиксирана"} седмица. Текущият режим остава до утвърждаване.</p>}</div>
      <div className="management-actions">
        {p.status !== "COMPLETED" && <button type="button" className="action-button secondary" disabled={actionsDisabled} onClick={() => mutate("action", { action: p.status === "PAUSED" ? "RESUME" : "PAUSE" })}>{p.status === "PAUSED" ? "Продължи с текущите настройки" : "Пауза"}</button>}
        {p.status !== "PAUSED" && p.status !== "COMPLETED" && <button type="button" className="action-button" disabled={actionsDisabled} onClick={() => mutate("action", { action: "REFRESH" })}>{busy || externalBusy ? "Обновяване…" : "Обнови сега"}</button>}
      </div>
    </div>
    {error?.revision === record.revision && <p className="management-error" role="alert">{error.message}</p>}
    {notice && <p className="management-notice" role="status">{notice}</p>}
    {record.stale && <p className="management-notice" role="status">{record.stale_reason} Последната версия е показана само за справка.</p>}
    {symptomHold && <SymptomNotice day={feedback.latest_report_day}/>}
    {checked.warnings.some(w => w.code !== "REPORTED_ILLNESS_OR_PAIN") && <aside className="management-notice" aria-label="Предупреждения и условия">
      <h3>Предупреждения и условия {p.proposal ? "за предложението" : "към текущия график"}</h3>
      <ul>{checked.warnings.filter(w => w.code !== "REPORTED_ILLNESS_OR_PAIN").map((warning, index) => <li key={`${warning.code}-${index}`}>{warning.message}</li>)}</ul>
    </aside>}
    {p.proposal && <PlanProposalReview key={record.revision} record={record} canEdit={canEdit} busy={busy || externalBusy}
      symptomHold={symptomHold} onApprove={() => mutate("action", { action: "APPROVE" })} renderDay={renderDay} today={today}/>}
    {fixed && p.monitoring && <details className="management-panel"><summary>Актуална проверка на Recovery · {p.monitoring.evaluated_on}</summary>
      <p>Готовност в началото на деня, %. Бъдещите стойности са прогноза за предложения вариант, а не потвърждение на запазените дози. При липсващо покритие стойността остава неизвестна.</p>
      <div className="management-table-wrap"><table><thead><tr><th>Ден</th>{COMPONENTS.map(z => <th key={z}>{z}</th>)}</tr></thead><tbody>{p.monitoring.days.map(d => <tr key={d.date}><th>{d.date}</th>{COMPONENTS.map(z => <td key={z}>{typeof d.readiness_before?.[z] === "number" ? d.readiness_before[z]!.toFixed(0) : "—"}</td>)}</tr>)}</tbody></table></div>
    </details>}
    {!p.proposal && p.mode === "AUTO" && p.status === "ACTIVE" && p.changes.length > 0 && <details className="management-panel"><summary>Автоматично приложени промени · {p.changes.length} дни</summary>
      <p>Тези корекции вече са включени в текущия график. Не чакат утвърждаване.</p>
      <ul>{p.changes.map(change => <li key={change.date}><strong>{change.date}:</strong> {change.before ? `${change.before} (${durationHms(change.before_minutes)}) → ` : ""}{change.after} ({durationHms(change.after_minutes)})<p>{change.reason}</p></li>)}</ul>
    </details>}
    <h3>Утвърден график</h3>
    {fixed && <p className="management-muted">Оценките в запазените задачи са от утвърждаването. Новото изпълнение се отчита отделно и не променя автоматично тези дози. {p.monitoring ? `Последна проверка: ${p.monitoring.evaluated_on}.` : ""}</p>}
    {p.status !== "COMPLETED" && hasProgramDays(display) && <div className={`management-days ${record.actionable ? "" : "management-reference"}`}><TrainingPlanWeek days={display.days} today={today} renderDay={day => <div key={day.date}>
      {p.monitoring?.days.find(d => d.date === day.date)?.status === "REVIEW_REQUIRED" && <p className="management-notice" role="status">Нужен е преглед на запазената задача. {p.monitoring.days.find(d => d.date === day.date)?.reason}</p>}
      {p.outcomes.some(o => o.date === day.date && ["RECORDED", "DIFFERENT_ACTIVITY"].includes(o.status)) && <p className="management-muted">Има отчетена активност за този ден. Запазената задача е показана за съпоставка, не като допълнителна тренировка.</p>}
      {renderDay(day)}
      {canEdit && ["ACTIVE", "REVIEW_REQUIRED"].includes(p.status) && day.date >= today && !p.outcomes.some(o => o.date === day.date && ["RECORDED", "DIFFERENT_ACTIVITY"].includes(o.status)) && day.status !== "EXISTING_ACTIVITY" && day.status !== "RACE" && <div className="management-day-actions">
        <button type="button" className="action-button secondary" disabled={actionsDisabled || record.stale} onClick={() => mutate("day", { date: day.date, action: "REST", note: "" })}>Почивка на {day.date}</button>
        {day.session && <button type="button" className="action-button secondary" disabled={actionsDisabled || record.stale} onClick={() => mutate("day", { date: day.date, action: "SKIP", note: "" })}>Пропускам тренировките за деня</button>}
        {p.decisions[day.date] && <button type="button" className="action-button secondary" disabled={actionsDisabled || record.stale} onClick={() => mutate("day", { date: day.date, action: "CLEAR", note: "" })}>Върни избора на системата</button>}
      </div>}
    </div>} /></div>}
    <p className="management-muted">Изпълнението се отчита от реалните активности. Приравненият товар и готовността се преизчисляват от тях; отбелязването „Пропускам“ не създава измислено натоварване.</p>
    {p.outcomes.length > 0 && <details className="management-panel"><summary>План и реално изпълнение</summary><div className="management-table-wrap"><table><thead><tr><th>Ден</th><th>Резултат</th><th>План</th><th>Изпълнено</th></tr></thead><tbody>{p.outcomes.slice(-28).reverse().map(o => <tr key={o.date}><td>{o.date}</td><td>{OUTCOME[o.status] ?? o.status}</td><td>{durationHms(o.planned_minutes)}</td><td>{o.actual_minutes === null ? "Няма данни" : durationHms(o.actual_minutes)}</td></tr>)}</tbody></table></div><p>Съпоставянето е по ден и средство. Наличието на активност не доказва, че всички предписани блокове са изпълнени.</p></details>}
    <details className="management-panel"><summary>История и пълна проследимост</summary><ol>{value.history.map(h => <li key={h.revision}><strong>Версия {h.revision}</strong> · {new Date(h.recorded_at).toLocaleString("bg-BG")}<p>{h.reason}</p></li>)}</ol>
      <button type="button" className="action-button secondary" onClick={() => {
        const url = URL.createObjectURL(new Blob([JSON.stringify(record, null, 2)], { type: "application/json" }));
        const link = document.createElement("a"); link.href = url; link.download = `onflows-active-plan-v${record.revision}.json`; link.click(); URL.revokeObjectURL(url);
      }}>Изтегли пълните данни за тази версия</button>
    </details>
    {value.capabilities?.unchanged_checks === true && <PlanCheckHistory refreshKey={checksRefreshKey} />}
  </section>;
}
