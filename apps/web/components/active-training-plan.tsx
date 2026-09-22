"use client";

import { TrainingPlanWeek } from "./training-plan-week";
import { SyncActionForm } from "./sync-action-form";
import { hasProgramDays } from "../lib/management-guidance";
import { useState, type ReactNode } from "react";
import { isRecord } from "../lib/training-status";
import { durationHms } from "../lib/duration-format";
import { parseActivePlanResponse, type ActivePlanResponse, type DraftDay } from "../lib/training-management";

const STATE: Record<string, string> = { ACTIVE: "Активна програма", PAUSED: "Програмата е на пауза", REVIEW_REQUIRED: "Промяна за преглед", COMPLETED: "Завършена програма" };
const OUTCOME: Record<string, string> = { RECORDED: "Има реално изпълнение", DIFFERENT_ACTIVITY: "Изпълнено е друго средство", MISSED: "Без изпълнена сесия", SKIPPED: "Пропусната", REST: "Почивка", UNKNOWN: "Липсват данни" };

export function ActiveTrainingPlan({ value, onChange, canEdit, today, renderDay }: {
  value: ActivePlanResponse; onChange: (v: ActivePlanResponse) => void; canEdit: boolean; today: string;
  renderDay: (day: DraftDay) => ReactNode;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const record = value.active;
  if (!record) return null;
  const p = record.payload;
  const display = p.proposal ?? p.plan;
  async function mutate(endpoint: string, body: Record<string, unknown>) {
    setBusy(true); setError("");
    try {
      const response = await fetch(`/api/athlete/management/${endpoint}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...body, expected_revision: record!.revision }) });
      const result: unknown = await response.json();
      if (!response.ok) throw new Error(isRecord(result) && typeof result.error === "string" ? result.error : "Програмата не беше променена.");
      onChange(parseActivePlanResponse(result));
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Програмата не беше променена."); }
    finally { setBusy(false); }
  }
  const actionsDisabled = !canEdit || busy;
  return <section className="management-active" aria-label="Активна тренировъчна програма">
    <div className="management-panel management-current-heading"><div><p className="eyebrow">Текущ план · версия {record.revision}</p><h2>{STATE[p.status]}</h2>
      <p>{p.mode === "AUTO" ? "Следващите дни се адаптират автоматично след нов анализ и при смяна на деня." : "Адаптациите се предлагат за утвърждаване."}</p><p>{p.reason}</p></div>
      <div className="management-actions">
        {p.status !== "COMPLETED" && <button type="button" className="action-button secondary" disabled={actionsDisabled} onClick={() => mutate("action", { action: p.status === "PAUSED" ? "RESUME" : "PAUSE" })}>{p.status === "PAUSED" ? "Продължи с текущите настройки" : "Пауза"}</button>}
        {p.status !== "PAUSED" && p.status !== "COMPLETED" && <button type="button" className="action-button" disabled={actionsDisabled} onClick={() => mutate("action", { action: "REFRESH" })}>{busy ? "Обновяване…" : "Обнови сега"}</button>}
      </div>
    </div>
    {error && <p className="management-error" role="alert">{error}</p>}
    {record.stale && <p className="management-notice" role="status">{record.stale_reason} Последната версия е показана само за справка.</p>}
    {p.status === "REVIEW_REQUIRED" && <div className="management-notice"><p>Предложението по-долу още не е действаща задача.</p>
      {p.proposal?.activation_eligible === true ? <button type="button" className="action-button" disabled={actionsDisabled || record.stale} onClick={() => mutate("action", { action: "APPROVE" })}>Утвърди актуалната адаптация</button> : canEdit ? <SyncActionForm scope="FULL" returnTo="/management" label="Обнови тренировките" /> : <p>Треньорът трябва да прегледа актуалните данни.</p>}
    </div>}
    {p.changes.length > 0 && <details className="management-panel"><summary>Какво се промени и защо · {p.changes.length} дни</summary><ul>{p.changes.map(change => <li key={change.date}><strong>{change.date}:</strong> {change.before ? `${change.before} → ` : ""}{change.after}<p>{change.reason}</p></li>)}</ul></details>}
    {p.status !== "COMPLETED" && hasProgramDays(display) && <div className={`management-days ${record.actionable ? "" : "management-reference"}`}><TrainingPlanWeek days={display.days} today={today} renderDay={day => <div key={day.date}>
      {renderDay(day)}
      {canEdit && ["ACTIVE", "REVIEW_REQUIRED"].includes(p.status) && day.date >= today && day.status !== "EXISTING_ACTIVITY" && day.status !== "RACE" && <div className="management-day-actions">
        <button type="button" className="action-button secondary" disabled={busy || record.stale} onClick={() => mutate("day", { date: day.date, action: "REST", note: "" })}>Почивка на {day.date}</button>
        {day.session && <button type="button" className="action-button secondary" disabled={busy || record.stale} onClick={() => mutate("day", { date: day.date, action: "SKIP", note: "" })}>Пропускам тренировките за деня</button>}
        {p.decisions[day.date] && <button type="button" className="action-button secondary" disabled={busy || record.stale} onClick={() => mutate("day", { date: day.date, action: "CLEAR", note: "" })}>Върни избора на системата</button>}
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
  </section>;
}
