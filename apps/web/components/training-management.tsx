"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { durationHms } from "../lib/duration-format";
import { isRecord } from "../lib/training-status";
import { WEEKDAYS } from "../lib/planning-profile";
import {
  CAPACITY_LABELS, COMPONENTS, PHASE_LABELS, defaultManagementProfile, parseDraftRecord, parseDrafts, parseManagementProfile,
  parseManagementProfileResponse, type DraftDay, type DraftRecord, type ManagementProfile, type ManagementProfileResponse,
} from "../lib/training-management";

const number = (value: unknown, digits = 1) => typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("bg-BG", { maximumFractionDigits: digits }) : "—";
const dateLabel = (day: string) => new Date(`${day}T12:00:00Z`).toLocaleDateString("bg-BG", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });
const datePlus = (day: string, days: number) => { const value = new Date(`${day}T12:00:00Z`); value.setUTCDate(value.getUTCDate() + days); return value.toISOString().slice(0, 10); };
const STATUS_LABELS: Record<string, string> = { TRAINING: "Тренировка", REST: "Почивка", EXISTING_ACTIVITY: "Има изпълнена активност", UNAVAILABLE: "Няма свободно време", RACE: "Състезание", REVIEW_REQUIRED: "Нужен е преглед" };
const LIMIT_LABELS: Record<string, string> = { METHOD_WORK_CAP: "Максимална основна работа за метода", DAILY_AVAILABLE_WORK: "Оставащо време след загрявка и разпускане", REMAINING_WEEKLY_WORK: "Оставащ седмичен обем за основна работа", LOW_ABSOLUTE_RECOVERY_CAP: "Лимит на възстановителната работа", TAPER_DAILY_WORK_CAP: "Лимит за предсъстезателно разтоварване" };
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

function DayCard({ day }: { day: DraftDay }) {
  const session = day.session;
  const evidence = session?.dose_evidence;
  return <article className={`management-day ${session ? "has-session" : ""}`}>
    <header><div><p className="management-day-date">{dateLabel(day.date)}</p><h3>{session?.title ?? STATUS_LABELS[day.status] ?? day.status}</h3></div>
      <span className="management-badge">{PHASE_LABELS[day.period] ?? day.period}{day.taper ? " · тейпър" : ""}</span></header>
    {session && <p className="management-session-total"><strong>{durationHms(session.total_minutes)}</strong> общо · {durationHms(session.main_work_minutes)} основна работа · {session.zone}</p>}
    <p>{day.explanation}</p>
    {session && <ol className="management-blocks">{session.blocks.map((block, index) => <li key={`${block.kind}-${index}`}><div><strong>{block.label}</strong><span>{durationHms(block.duration_min)} · {block.zone}</span></div><p>{block.instructions}</p>
      {(block.target_hr_bpm !== null || block.target_speed_kmh !== null) && <small>{block.target_hr_bpm !== null ? `${number(block.target_hr_bpm, 0)} уд./мин` : ""}{block.target_hr_bpm !== null && block.target_speed_kmh !== null ? " · " : ""}{block.target_speed_kmh !== null ? `${number(block.target_speed_kmh)} km/h` : ""}</small>}
    </li>)}</ol>}
    <details className="management-detail"><summary>Защо тази задача и доза?</summary>
      {evidence && <><p><strong>Основа: {CAPACITY_LABELS[evidence.capacity_source] ?? evidence.capacity_source}.</strong></p><p>{evidence.explanation}</p>
        <dl className="management-facts"><div><dt>Непрекъсната устойчивост</dt><dd>{durationHms(evidence.capacity_minutes)}</dd></div><div><dt>Дял според метода</dt><dd>{number(evidence.fraction * 100)}%</dd></div><div><dt>Първоначално поискана работа</dt><dd>{durationHms(evidence.requested_work_minutes)}</dd></div><div><dt>Предписана основна работа</dt><dd>{durationHms(evidence.prescribed_work_minutes)}</dd></div></dl>
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

export function TrainingManagement({ athleteName, canEdit, initialProfile, initialDrafts, today }: {
  athleteName: string; canEdit: boolean; initialProfile: ManagementProfileResponse; initialDrafts: DraftRecord[]; today: string;
}) {
  const [saved, setSaved] = useState(initialProfile);
  const [profile, setProfile] = useState<ManagementProfile>(initialProfile.profile ?? defaultManagementProfile(today));
  const [drafts, setDrafts] = useState(initialDrafts);
  const [selected, setSelected] = useState(0);
  const [startDate, setStartDate] = useState(datePlus(today, 1));
  const [busy, setBusy] = useState<"save" | "generate" | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const draft = drafts[selected];
  const dirty = JSON.stringify(profile) !== JSON.stringify(saved.profile);
  const update = <K extends keyof ManagementProfile>(key: K, value: ManagementProfile[K]) => setProfile(previous => ({ ...previous, [key]: value }));
  const optionalNumber = (value: string) => value === "" ? null : Number(value);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(""); setNotice(""); setBusy("save");
    try {
      const checked = parseManagementProfile(profile);
      const result = parseManagementProfileResponse(await requestJson("profile", "PUT", { profile: checked, expected_revision: saved.revision }));
      setSaved(result); if (result.profile) setProfile(result.profile);
      setDrafts(previous => previous.map(item => ({ ...item, stale: true, stale_reason: "Профилът е променен след създаването на този проект." })));
      setNotice(`Профилът е запазен като версия ${result.revision}. Промените ще се използват при следващото генериране. Съществуващите програми запазват своите настройки.`);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Профилът не беше записан."); }
    finally { setBusy(null); }
  }

  async function generate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(""); setNotice(""); setBusy("generate");
    try {
      const currentHistory = parseDrafts(await requestJson(`drafts?start_date=${encodeURIComponent(startDate)}`, "GET"));
      const existing = currentHistory.filter(item => item.payload.start_date === startDate).reduce((maximum, item) => Math.max(maximum, item.revision), 0);
      const record = parseDraftRecord(await requestJson("generate", "POST", { start_date: startDate, expected_profile_revision: saved.revision, expected_draft_revision: existing }));
      setDrafts(previous => [record, ...previous.filter(item => !(item.entry_key === record.entry_key && item.revision === record.revision))]); setSelected(0);
      setNotice(record.payload.status === "BLOCKED" ? "Програмата изисква допълнителни данни. Причините са показани по-долу." : "Новият проект е готов за преглед. Не е публикуван в тренировъчния календар.");
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Програмата не беше създадена."); }
    finally { setBusy(null); }
  }

  function exportDraft() {
    if (!draft) return;
    const blob = new Blob([JSON.stringify(draft, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob); const anchor = document.createElement("a");
    anchor.href = url; anchor.download = `onflows-plan-${draft.payload.start_date}-v${draft.revision}.json`; anchor.click(); URL.revokeObjectURL(url);
  }

  return <main className="activities-page management-page">
    <header className="activities-hero"><div className="activities-title"><div><p className="eyebrow">Индивидуална подготовка</p><h1>Управление и програма</h1><p>{athleteName} · Седемдневен проект за треньорски преглед</p></div></div></header>
    <p className="management-intro">Периодът задава целта. Скорост–време оценява капацитета, методът определя дозата, а натоварването и възстановяването определят приложимостта ѝ.</p>
    <p className="management-notice">Първа версия: конкретни задачи в Z1–Z3, с консервативни граници спрямо досегашното натоварване и C40. Z4, Z5 и силовите сесии предстоят. Възрастта, стажът и състезателната продължителност се записват в профила; автоматичната прогресия според тях предстои.</p>
    {!canEdit && <p className="management-notice">Имате достъп за преглед. Редактирането на профила и създаването на програма изискват право за промяна на плана.</p>}
    {error && <p className="management-error" role="alert">{error}</p>}{notice && <p className="management-notice" role="status">{notice}</p>}
    <details className="management-profile management-panel" open={!saved.configured}>
      <summary>Профил и настройки <span>{saved.configured ? `Версия ${saved.revision}` : "Нужна е начална настройка"}</span></summary>
      <form onSubmit={save}><fieldset disabled={!canEdit || busy !== null}><div className="management-form-grid">
        <label>Основен спорт<select value={profile.sport} onChange={event => { const sport = event.target.value as ManagementProfile["sport"]; setProfile(previous => ({ ...previous, sport, actual_sport: sport })); }}><option value="Run">Бягане</option><option value="NordicSki">Ски бягане</option></select></label>
        <label>Средство за тази програма<select value={profile.actual_sport} onChange={event => update("actual_sport", event.target.value as ManagementProfile["actual_sport"])}><option value="Run">Бягане</option>{profile.sport === "NordicSki" && <><option value="NordicSki">Ски бягане</option><option value="RollerSki">Ролкови ски</option></>}</select></label>
        <label>Дисциплина<input required maxLength={100} value={profile.discipline} placeholder="Напр. 5000 m или 10 km свободен стил" onChange={event => update("discipline", event.target.value)} /></label>
        <label>Очаквана продължителност на старта, мин<input type="number" min="0.1" max="1440" step="0.1" value={profile.race_duration_min ?? ""} onChange={event => update("race_duration_min", optionalNumber(event.target.value))} /></label>
        <label>Възраст, години<input type="number" min="10" max="100" value={profile.age_years ?? ""} onChange={event => update("age_years", optionalNumber(event.target.value))} /></label>
        <label>Спортен стаж, години<input type="number" min="0" max="85" step="0.5" value={profile.training_experience_years ?? ""} onChange={event => update("training_experience_years", optionalNumber(event.target.value))} /></label>
        <label>Начало на подготовката<input required type="date" value={profile.program_start} onChange={event => update("program_start", event.target.value)} /></label>
        <label>Край на периода<input required type="date" min={profile.program_start} value={profile.program_end} onChange={event => update("program_end", event.target.value)} /></label>
      </div><h3>Налично време по дни</h3><p className="management-muted">Минути за цялата сесия, включително загрявка и разпускане. Нула означава свободен от тренировки ден.</p>
      <div className="management-week-grid">{WEEKDAYS.map((day, index) => <label key={day}>{day}<input aria-label={`${day}, налични минути`} required type="number" min="0" max="360" step="5" value={profile.available_minutes[index]} onChange={event => update("available_minutes", profile.available_minutes.map((value, dayIndex) => dayIndex === index ? Number(event.target.value) : value))} /></label>)}</div>
      <label className="management-check"><input type="checkbox" checked={profile.recent_weekly_hours !== null} onChange={event => update("recent_weekly_hours", event.target.checked ? [0, 0, 0, 0] : null)} />Въвеждам обема за последните четири завършени седмици</label>
      <p className="management-muted">Допълва началната картина при липса на достатъчно история. Не замества данните за възстановяване.</p>
      {profile.recent_weekly_hours && <div className="management-form-grid">{profile.recent_weekly_hours.map((hours, index) => <label key={index}>{index === 3 ? "Последна завършена седмица" : `Преди ${4 - index} седмици`}, часове<input required type="number" min="0" max="80" step="0.1" value={hours} onChange={event => update("recent_weekly_hours", profile.recent_weekly_hours!.map((value, week) => week === index ? Number(event.target.value) : value))} /></label>)}</div>}
      <details className="management-detail"><summary>Методически настройки и граници</summary><p>Тези начални треньорски правила се записват с версията на профила. Настройките не променят вече създадени програми.</p><div className="management-form-grid">
        <label>Вработване, дни<input type="number" min="0" max="21" placeholder="Автоматично" value={profile.reentry_days ?? ""} onChange={event => update("reentry_days", optionalNumber(event.target.value))} /></label>
        <label>Тейпър в края на предсъстезателния период, дни<input required type="number" min="0" max="21" value={profile.taper_days} onChange={event => update("taper_days", Number(event.target.value))} /></label>
        <label>Максимум ключови сесии седмично<input required type="number" min="0" max="3" value={profile.max_key_sessions_per_week} onChange={event => update("max_key_sessions_per_week", Number(event.target.value))} /></label>
        <label>Изграждаща доза Z1–Z3, %<input required type="number" min="50" max="60" step="1" value={Math.round(profile.building_fraction * 100)} onChange={event => update("building_fraction", Number(event.target.value) / 100)} /></label>
        <label>Поддържаща доза Z1–Z3, %<input required type="number" min="30" max="40" step="1" value={Math.round(profile.maintenance_fraction * 100)} onChange={event => update("maintenance_fraction", Number(event.target.value) / 100)} /></label>
        <label>Изграждаща доза при вработване, %<input required type="number" min="40" max="50" step="1" value={Math.round(profile.reentry_fraction * 100)} onChange={event => update("reentry_fraction", Number(event.target.value) / 100)} /></label>
        <label>Таван на възстановителната сесия, мин<input required type="number" min="5" max="45" value={profile.recovery_session_cap_min} onChange={event => update("recovery_session_cap_min", Number(event.target.value))} /></label>
      </div><label className="management-check"><input type="checkbox" checked={profile.allow_expert_fallback} onChange={event => update("allow_expert_fallback", event.target.checked)} />Разрешавам експертен Tref при недостатъчно надеждна оценка от скорост–време</label>
      <p className="management-muted">Tref тук означава непрекъсната устойчивост при конкретна интензивност и средство. Историческият обем и C40 са различни величини.</p></details>
      <div className="management-actions"><button className="action-button" type="submit" disabled={!dirty}>{busy === "save" ? "Запазване…" : "Запази профила"}</button><Link href="/planning">Стартове и лагери в календара →</Link></div>
      </fieldset></form>
    </details>
    <section className="management-panel"><h2>Следващите седем дни</h2><form className="management-generate" onSubmit={generate}><label>Начална дата<input aria-label="Начална дата на програмата" required type="date" min={saved.profile && saved.profile.program_start > today ? saved.profile.program_start : today} max={saved.profile && datePlus(saved.profile.program_end, -6) < datePlus(today, 7) ? datePlus(saved.profile.program_end, -6) : datePlus(today, 7)} value={startDate} onChange={event => setStartDate(event.target.value)} disabled={!canEdit || busy !== null} /></label><button className="action-button" type="submit" disabled={!canEdit || busy !== null || !saved.configured || dirty}>{busy === "generate" ? "Подготвям програмата…" : "Създай проект за преглед"}</button></form>
      <p className="management-muted">Началото може да е днес или в следващите седем дни. Целият проект трябва да се побира в периода на подготовка.</p>
      {dirty && <p className="management-muted">Запазете профила, за да използвате тези настройки в новия проект.</p>}
      <p className="management-muted">Проектът не се изпраща към Intervals и не променя автоматично календара. Wellness остава диагностичен; готовността използва само натоварването.</p>
    </section>
    {draft ? <section className="management-plan" aria-label="Проект на тренировъчна програма"><div className="management-plan-heading"><div><h2>{dateLabel(draft.payload.start_date)} – {dateLabel(draft.payload.end_date)}</h2><p>{draft.payload.status === "BLOCKED" ? "Нужни са допълнителни данни" : draft.payload.status === "LIMITED_DRAFT" ? "Проект с ограничения — прегледайте причините" : "Проект за треньорски преглед"} · Версия {draft.revision}</p></div>
      <label>Запазени проекти<select value={selected} onChange={event => setSelected(Number(event.target.value))}>{drafts.map((item, index) => <option key={`${item.entry_key}-${item.revision}`} value={index}>{item.payload.start_date} · v{item.revision}{item.recorded_at ? ` · ${new Date(item.recorded_at).toLocaleString("bg-BG")}` : ""}</option>)}</select></label></div>
      {draft.stale === true && <p className="management-notice" role="status">{draft.stale_reason ?? "Входните данни са променени — създайте нов проект."}</p>}
      {draft.stale === null && <p className="management-notice" role="status">Актуалността на входните данни не е потвърдена. Прегледайте данните преди използване.</p>}
      {draft.payload.warnings.length > 0 && <div className="management-notice"><ul>{draft.payload.warnings.map((warning, index) => <li key={`${warning.code}-${index}`}>{warning.message}</li>)}</ul></div>}
      <div className="management-days">{draft.payload.days.map(day => <DayCard key={day.date} day={day} />)}</div>
      <details className="management-panel management-detail"><summary>Периодизация, източници и настройки на този проект</summary><p>Програмата запазва входните данни и версиите, с които е изчислена. По-късни настройки се използват в следващ проект.</p>
        <PeriodizationTable value={draft.payload.periodization} />
        <dl className="management-facts"><div><dt>Управляващ модел</dt><dd>{draft.payload.engine_version}</dd></div><div><dt>Скорост–време</dt><dd>{String(draft.payload.source.speed_model_version ?? "Няма версия")}</dd></div><div><dt>Recovery</dt><dd>{String(draft.payload.source.recovery_model_version ?? "Няма версия")}</dd></div><div><dt>Дни история</dt><dd>{number(draft.payload.source.history_days, 0)}</dd></div></dl>
        <details><summary>Точни параметри и периоди</summary><pre>{JSON.stringify({ periodization: draft.payload.periodization, parameters: draft.payload.parameters, source: draft.payload.source, catalog: draft.payload.catalog }, null, 2)}</pre></details>
        <button type="button" className="action-button secondary" onClick={exportDraft}>Изтегли пълния отчет</button>
      </details>
    </section> : <section className="management-panel"><h2>Все още няма проект</h2><p>Попълнете профила и стартовете. Първата програма ще покаже конкретните задачи и причините за избраната доза.</p></section>}
  </main>;
}
