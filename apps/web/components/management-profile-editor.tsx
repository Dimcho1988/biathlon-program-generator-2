"use client";
import Link from "next/link";
import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { WEEKDAYS } from "../lib/planning-profile";
import { isRecord } from "../lib/training-status";
import { defaultManagementProfile, parseManagementProfile, parseManagementProfileResponse, type ManagementProfile, type ManagementProfileResponse } from "../lib/training-management";
import { ManagementRules } from "./management-rules";

export function ManagementProfileEditor({ initialProfile, today, canEdit = true }: { initialProfile: ManagementProfileResponse; today: string; canEdit?: boolean }) {
  const router = useRouter();
  const [saved, setSaved] = useState(initialProfile);
  const [profile, setProfile] = useState<ManagementProfile>(initialProfile.profile ?? defaultManagementProfile(today));
  const [step, setStep] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const dirty = JSON.stringify(profile) !== JSON.stringify(saved.profile);
  const update = <K extends keyof ManagementProfile>(key: K, value: ManagementProfile[K]) => setProfile(previous => ({ ...previous, [key]: value }));
  const optionalNumber = (value: string) => value === "" ? null : Number(value);
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(""); setNotice("");
    if (step === 1) { setStep(2); return; }
    setBusy(true);
    try {
      const checked = parseManagementProfile({ ...profile, discipline: profile.discipline.trim() });
      const response = await fetch("/api/athlete/management/profile", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ profile: checked, expected_revision: saved.revision }) });
      const data: unknown = await response.json();
      if (!response.ok) throw new Error(isRecord(data) && typeof data.error === "string" ? data.error : "Профилът не беше записан.");
      const result = parseManagementProfileResponse(data);
      setSaved(result); if (result.profile) setProfile(result.profile);
      setNotice("Профилът е запазен. Добави стартове по желание или отвори тренировъчния план.");
      router.refresh();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Профилът не беше записан."); }
    finally { setBusy(false); }
  }
  return <section className="management-panel management-profile" id="basic-profile">
    <p className="eyebrow">{saved.configured ? "Профилът е попълнен" : "Начална настройка"} · стъпка {step} от 2</p>
    {error && <p className="management-error" role="alert">{error}</p>}
    {notice && <p className="management-notice" role="status">{notice}</p>}
      <form onSubmit={save} onInvalidCapture={event => { let parent = (event.target as HTMLElement).parentElement; while (parent) { if (parent instanceof HTMLDetailsElement) parent.open = true; parent = parent.parentElement; } }}><fieldset disabled={!canEdit || busy}>{step === 1 && <><h2>1. Спорт и цел</h2><p className="management-muted">Задължителни: спорт, средство, дисциплина и период на подготовката.</p><div className="management-form-grid">
        <label>Основен спорт · задължително<select value={profile.sport} onChange={event => { const sport = event.target.value as ManagementProfile["sport"]; setProfile(previous => ({ ...previous, sport, actual_sport: sport })); }}><option value="Run">Бягане</option><option value="NordicSki">Ски бягане</option></select></label>
        <label>Средство · задължително<select value={profile.actual_sport} onChange={event => update("actual_sport", event.target.value as ManagementProfile["actual_sport"])}><option value="Run">Бягане</option>{profile.sport === "NordicSki" && <><option value="NordicSki">Ски бягане</option><option value="RollerSki">Ролкови ски</option></>}</select></label>
        <label>Дисциплина · задължително<input required maxLength={100} value={profile.discipline} placeholder="Напр. 5000 m или 10 km свободен стил" onChange={event => update("discipline", event.target.value)} /></label>
        <label>Продължителност на старта, мин · по желание<input type="number" min="0.1" max="1440" step="0.1" value={profile.race_duration_min ?? ""} onChange={event => update("race_duration_min", optionalNumber(event.target.value))} /></label>
        <label>Възраст, години · по желание<input type="number" min="10" max="100" value={profile.age_years ?? ""} onChange={event => update("age_years", optionalNumber(event.target.value))} /></label>
        <label>Спортен стаж, години · по желание<input type="number" min="0" max="85" step="0.5" value={profile.training_experience_years ?? ""} onChange={event => update("training_experience_years", optionalNumber(event.target.value))} /></label>
        <label>Начало · задължително<input required type="date" value={profile.program_start} onChange={event => update("program_start", event.target.value)} /></label>
        <label>Край · задължително<input required type="date" min={profile.program_start} value={profile.program_end} onChange={event => update("program_end", event.target.value)} /></label>
      </div><p className="management-muted">Продължителността на старта помага за специфичността. Възрастта и стажът са нужни, ако включваш индивидуални интервали Z4/Z5.</p><button className="action-button" type="submit">Напред: време за тренировки →</button></>}{step === 2 && <><h2>2. Време и автоматична работа</h2><h3>Налично време по дни · задължително</h3><p className="management-muted">Провери предложените минути за цялата сесия. Това е свободното ти време, а не предписан товар. Нула означава ден без тренировка.</p>
      <div className="management-week-grid">{WEEKDAYS.map((day, index) => <label key={day}>{day}<input aria-label={`${day}, налични минути`} required type="number" min="0" max="360" step="5" value={profile.available_minutes[index]} onChange={event => update("available_minutes", profile.available_minutes.map((value, dayIndex) => dayIndex === index ? Number(event.target.value) : value))} /></label>)}</div>
      <h3>Досегашен обем · автоматично от историята</h3><label className="management-check"><input type="checkbox" checked={profile.recent_weekly_hours !== null} onChange={event => update("recent_weekly_hours", event.target.checked ? [0, 0, 0, 0] : null)} />Въвеждам обема за последните четири завършени седмици</label>
      <p className="management-muted">Попълва се само ако няма достатъчно внесена история. Тези четири стойности не заместват данните за възстановяване.</p>
      {profile.recent_weekly_hours && <div className="management-form-grid">{profile.recent_weekly_hours.map((hours, index) => <label key={index}>{index === 3 ? "Последна завършена седмица" : `Преди ${4 - index} седмици`}, часове<input required type="number" min="0" max="80" step="0.1" value={hours} onChange={event => update("recent_weekly_hours", profile.recent_weekly_hours!.map((value, week) => week === index ? Number(event.target.value) : value))} /></label>)}</div>}
      <h3>Как да работи програмата?</h3><label>Адаптиране<select value={profile.adaptation_mode} onChange={e => update("adaptation_mode", e.target.value as "AUTO" | "REVIEW")}><option value="AUTO">Автоматично според изпълнените тренировки</option><option value="REVIEW">С преглед и утвърждаване на промените</option></select></label><label className="management-check"><input type="checkbox" checked={profile.auto_import_enabled} onChange={e => update("auto_import_enabled", e.target.checked)} />Обновявай активностите автоматично, докато програмата работи</label><p className="management-muted">Автоматично: разпределение на периодите и акцентите, дозиране и проверки на готовността. Първата програма се започва след твоя преглед.</p><details className="management-detail"><summary>Разширени настройки · по желание</summary><p>Началните правила са попълнени. Z4/Z5 и сила се включват само с подходящ профил; липсата им не блокира останалите методи.</p><p>Тези начални треньорски правила се записват с версията на профила. Настройките не променят вече създадени програми.</p><div className="management-form-grid">
        <label>Вработване, дни<input type="number" min="0" max="21" placeholder="Автоматично" value={profile.reentry_days ?? ""} onChange={event => update("reentry_days", optionalNumber(event.target.value))} /></label>
        <label>Тейпър в края на предсъстезателния период, дни<input required type="number" min="0" max="21" value={profile.taper_days} onChange={event => update("taper_days", Number(event.target.value))} /></label>
        <label>Максимум ключови сесии седмично<input required type="number" min="0" max="3" value={profile.max_key_sessions_per_week} onChange={event => update("max_key_sessions_per_week", Number(event.target.value))} /></label>
        <label>Изграждаща доза Z1–Z3, %<input required type="number" min="50" max="60" step="1" value={Math.round(profile.building_fraction * 100)} onChange={event => update("building_fraction", Number(event.target.value) / 100)} /></label>
        <label>Поддържаща доза Z1–Z3, %<input required type="number" min="30" max="40" step="1" value={Math.round(profile.maintenance_fraction * 100)} onChange={event => update("maintenance_fraction", Number(event.target.value) / 100)} /></label>
        <label>Изграждаща доза при вработване, %<input required type="number" min="40" max="50" step="1" value={Math.round(profile.reentry_fraction * 100)} onChange={event => update("reentry_fraction", Number(event.target.value) / 100)} /></label>
        <label>Таван на възстановителната сесия, мин<input required type="number" min="5" max="45" value={profile.recovery_session_cap_min} onChange={event => update("recovery_session_cap_min", Number(event.target.value))} /></label>
      </div><label className="management-check"><input type="checkbox" checked={profile.allow_expert_fallback} onChange={event => update("allow_expert_fallback", event.target.checked)} />Разрешавам експертен Tref при недостатъчно надеждна оценка от скорост–време</label>
      <ManagementRules profile={profile} onChange={setProfile} today={today} hideAutomation />
      <p className="management-muted">Tref тук означава непрекъсната устойчивост при конкретна интензивност и средство. Историческият обем и C40 са различни величини.</p></details>
      <div className="management-actions"><button className="action-button secondary" type="button" onClick={() => setStep(1)}>← Спорт и цел</button><button className="action-button" type="submit" disabled={!dirty}>{busy ? "Запазване…" : "Запази профила"}</button></div></>}
      </fieldset></form>

    {saved.configured && !dirty && <div className="management-actions"><a href="#planning-calendar">3. Стартове и лагери · по желание ↓</a><Link className="action-button secondary" href="/management">Към тренировъчния план →</Link></div>}
  </section>;
}
