"use client";

import { COMPONENTS, type ManagementProfile, type IntervalDoseProfile } from "../lib/training-management";

export function ManagementRules({ profile, onChange, today, hideAutomation = false }: {
  profile: ManagementProfile; onChange: (profile: ManagementProfile) => void; today: string; hideAutomation?: boolean;
}) {
  const update = <K extends keyof ManagementProfile>(key: K, value: ManagementProfile[K]) => onChange({ ...profile, [key]: value });
  const interval = (index: number, patch: Partial<IntervalDoseProfile>) => update("interval_profiles", profile.interval_profiles.map((p, i) => i === index ? { ...p, ...patch } : p));
  return <>
    <div className="management-form-grid">
      {!hideAutomation && <label>След утвърждаване<select value={profile.adaptation_mode} onChange={e => update("adaptation_mode", e.target.value as "AUTO" | "REVIEW")}><option value="AUTO">Адаптирай следващите дни автоматично</option><option value="REVIEW">Предлагай промените за преглед</option></select></label>}
      <label>Преход след последния основен старт, дни<input type="number" min="0" max="28" value={profile.transition_days} onChange={e => update("transition_days", Number(e.target.value))} /></label>
    </div>
    {!hideAutomation && <label className="management-check"><input type="checkbox" checked={profile.auto_import_enabled} onChange={e => update("auto_import_enabled", e.target.checked)} />Обновявай свързаните активности автоматично — до веднъж на час, докато програмата работи</label>}
    <p className="management-muted">Целите и вълната са в стъпка 3. Тук се задават методът и дозата; Recovery остава отделна проверка.</p>
    <details className="management-detail"><summary>Индивидуални цели по компоненти</summary>
      <p>Обичайно системата извежда целите от реалната история и периода. Попълнете тук само изрична треньорска цел — включително при въвеждане на нов компонент. Това са седмични приравнени минути, а не Tref или продължителност на тренировка.</p>
      <div className="management-form-grid">{COMPONENTS.map(zone => <label key={zone}>{zone} · седмична цел<input type="number" min="0" max="3000" step="0.5" placeholder="Автоматично" value={profile.component_targets_weekly[zone] ?? ""} onChange={e => {
        const targets = { ...profile.component_targets_weekly };
        if (e.target.value === "") delete targets[zone]; else targets[zone] = Number(e.target.value);
        update("component_targets_weekly", targets);
      }} /></label>)}</div>
    </details>
    <details className="management-detail"><summary>Обща силова подготовка · {profile.strength_enabled ? "включена" : "не е включена"}</summary>
      <label className="management-check"><input type="checkbox" checked={profile.strength_enabled} onChange={e => update("strength_enabled", e.target.checked)} />Включи общия силов профил</label>
      <p>9 упражнения за долна част, торс и горна част. Работа по 20 секунди, 30 секунди преход, 2 минути между кръговете. Използвайте усвоени, безболезнени движения и съпротивление с поне 3 качествени повторения в резерв.</p>
      <p className="management-muted">Силата има собствен бюджет и възстановяване. Пулсът от силовата част не добавя втори аеробен товар. В комбинирана сесия остава само един кръг и намалена аеробна доза.</p>
      <label>Максимум кръгове<select value={profile.strength_circuits} onChange={e => update("strength_circuits", Number(e.target.value))}><option value={2}>2</option><option value={3}>3</option></select></label>
    </details>
    <details className="management-detail"><summary>Интервали Z4/Z5 · {profile.interval_profiles.length ? `${profile.interval_profiles.length} профил(а)` : profile.planning_controls?.automatic_intervals!==false?"автоматични варианти":"нужен е индивидуален профил"}</summary>
      {profile.planning_controls&&<><label className="management-check"><input type="checkbox" checked={profile.planning_controls.automatic_intervals!==false} onChange={e=>update("planning_controls",{...profile.planning_controls!,automatic_intervals:e.target.checked})}/>Разреши ограничените автоматични варианти за Z4/Z5</label><p>Z4: 3–6 × 3 мин / 3 мин, общ работен бюджет до 120% от капацитета. Z5: 6–20 × 30 сек / 30 сек, до 150%. Всеки профил има резерв от две отсечки. Процентите за Z1–Z3 не се прилагат. Z4 използва скорост–време или Tref за средството; автоматична Z5 изисква отделна скорошна максимална опора. Нужна е скорошна работа в съответната зона; 7/40 и Recovery проверяват всяка доза. Това са начални треньорски правила. Индивидуалният профил по-долу има предимство.</p></>}
      <p>Тук се задава устойчивостта при конкретно описано усилие за избраното средство. Тя не се извежда от пиков пулс. Тези профили са за възрастни с поне една година тренировъчен опит. Нужни са попълнени възраст и стаж.</p>
      <p className="management-muted">Повторенията, почивките и общият дял се проверяват заедно. Над 100% е разрешимо само за сумарната работа в този профил. Индивидуалната опора се преглежда отново след 42 дни; това е видима начална настройка.</p>
      {profile.interval_profiles.map((p, index) => <section key={p.zone} className="management-panel"><h4>{p.zone} · {p.sport === "Run" ? "Бягане" : p.sport === "NordicSki" ? "Ски бягане" : "Ролкови ски"}</h4>
        <label>Цел на интервалния профил<select value={p.goal??"AEROBIC_POWER"} onChange={e=>interval(index,{goal:e.target.value as IntervalDoseProfile["goal"]})}><option value="AEROBIC_POWER">Аеробна мощност</option>{p.zone==="Z4"&&<option value="THRESHOLD">Прагова работа · индивидуално определено усилие</option>}</select></label>
        <label>Усилието, за което важи устойчивостта<input minLength={8} maxLength={250} value={p.effort} placeholder="Опишете повторяемото усилие и качеството на изпълнение" onChange={e => interval(index, { effort: e.target.value })} /></label>
        <div className="management-form-grid">
          <label>Непрекъсната устойчивост при това усилие, мин<input type="number" min="0.1" max="60" step="0.1" value={p.continuous_capacity_min || ""} onChange={e => interval(index, { continuous_capacity_min: Number(e.target.value) })} /></label>
          <label>Дата на оценката<input type="date" max={today} value={p.assessed_on} onChange={e => interval(index, { assessed_on: e.target.value })} /></label>
          <label>Една работна отсечка, секунди<input type="number" min="15" max="360" value={p.work_seconds} onChange={e => interval(index, { work_seconds: Number(e.target.value) })} /></label>
          <label>Леко движение между отсечките, секунди<input type="number" min="15" max="600" value={p.recovery_seconds} onChange={e => interval(index, { recovery_seconds: Number(e.target.value) })} /></label>
          <label>Минимален брой повторения<input type="number" min="2" max="20" value={p.min_repetitions} onChange={e => interval(index, { min_repetitions: Number(e.target.value) })} /></label>
          <label>Максимален брой повторения<input type="number" min="2" max="20" value={p.max_repetitions} onChange={e => interval(index, { max_repetitions: Number(e.target.value) })} /></label>
          <label>Обща работа, до % от устойчивостта<input type="number" min="1" max="300" value={Math.round(p.total_capacity_ratio * 100)} onChange={e => interval(index, { total_capacity_ratio: Number(e.target.value) / 100 })} /></label>
          <label>Резерв в качествени повторения<input type="number" min="1" max="4" value={p.reserve_repetitions} onChange={e => interval(index, { reserve_repetitions: Number(e.target.value) })} /></label>
          <label>Треньорски зададена скорост, km/h<input type="number" min="0.1" max="80" step="0.1" placeholder="По желание, за съпоставим терен" value={p.target_speed_kmh ?? ""} onChange={e => interval(index, { target_speed_kmh: e.target.value === "" ? null : Number(e.target.value) })} /></label>
        </div>
        <label className="management-check"><input type="checkbox" checked={p.speed_basis === "FLAT_EQUIVALENT"} onChange={e => interval(index, { speed_basis: e.target.checked ? "FLAT_EQUIVALENT" : "ACTUAL" })} />Скоростта е равнинна опора, съпоставима с тестовете в скорост–време</label><p className="management-muted">При подкрепена крива тя определя устойчивостта първа. Въведената оценка остава резерв. Равнинната скорост не е пряка цел по наклон.</p>
        <button type="button" className="action-button secondary" onClick={() => update("interval_profiles", profile.interval_profiles.filter((_, i) => i !== index))}>Премахни профила за {p.zone}</button>
      </section>)}
      <div className="management-actions">{(["Z4", "Z5"] as const).filter(zone => !profile.interval_profiles.some(p => p.zone === zone)).map(zone => <button key={zone} type="button" className="action-button secondary" onClick={() => update("interval_profiles", [...profile.interval_profiles, {
        zone, sport: profile.actual_sport, continuous_capacity_min: 0, assessed_on: today, effort: "",
        work_seconds: zone === "Z4" ? 180 : 30, recovery_seconds: zone === "Z4" ? 180 : 30,
        min_repetitions: zone === "Z4" ? 3 : 6, max_repetitions: zone === "Z4" ? 5 : 12,
        total_capacity_ratio: 1, reserve_repetitions: 2, target_speed_kmh: null,
      }])}>Добави профил за {zone}</button>)}</div>
      <p className="management-muted">Стойностите в новата форма са за редактиране. Профилът не може да бъде записан без конкретна оценка на устойчивостта и описание на усилието.</p>
    </details>
  </>;
}
