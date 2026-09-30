"use client";
import { useState } from "react";
import type { ManagementProfile } from "../lib/training-management";
import { LACTATE_ZONES, defaultNeuromuscular, type LactateProfile, type LactateStage } from "../lib/training-observations";

const sports = { Run: "Бягане", NordicSki: "Ски бягане", RollerSki: "Ролкови ски" };
const days = ["Пон", "Вт", "Ср", "Чет", "Пет", "Съб", "Нед"];
const optional = (v: string) => v === "" ? null : Number(v);
const stage = (): LactateStage => ({ duration_min: 4, hr_bpm: null, speed_kmh: null, lactate_mmol: 0 });

export function TrainingObservationSettings({ profile, onChange, today }: { profile: ManagementProfile; onChange: (p: ManagementProfile) => void; today: string }) {
  const [sport, setSport] = useState<LactateProfile["sport"]>(profile.actual_sport);
  const profiles = profile.lactate_profiles ?? [];
  const current = profiles.find(p => p.sport === sport);
  const update = (p: LactateProfile | null) => onChange({ ...profile, lactate_profiles: [...profiles.filter(v => v.sport !== sport), ...(p ? [p] : [])] });
  const nms = profile.neuromuscular ?? defaultNeuromuscular();
  const updateNms = (change: Partial<typeof nms>) => onChange({ ...profile, neuromuscular: { ...nms, ...change } });
  return <>
    <details className="training-observation-settings"><summary>Лактат · ориентири и тестове по желание</summary>
      <p>Личните стойности имат предимство пред общите ориентири и могат да бъдат извън тях. Измерванията след тренировка се записват към активността в „Оценка и реакция“.</p>
      <label className="management-check"><input type="checkbox" checked={profile.lactate_guidance_enabled ?? true} onChange={e => onChange({ ...profile, lactate_guidance_enabled: e.target.checked })}/>Показвай лактатните ориентири в програмата</label>
      <label>Средство за лактатния профил<select value={sport} onChange={e => setSport(e.target.value as LactateProfile["sport"])}>{Object.entries(sports).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      {!current ? <><p>Няма личен лактатен профил за това средство.</p><button type="button" className="action-button secondary" onClick={() => update({ sport, source: "MANUAL", assessed_on: today, protocol: "", device: "", note: "", stages: [], zone_ranges: {} })}>Добави индивидуални ориентири или тест</button></> : <>
        <div className="management-form-grid">
          <label>Източник на личните ориентири<select value={current.source} onChange={e => update({ ...current, source: e.target.value as LactateProfile["source"], zone_ranges: {}, stages: e.target.value === "TEST" ? [stage(), stage(), stage()] : [] })}><option value="MANUAL">Ръчно зададени към зоните</option><option value="TEST">Лактатен тест</option></select></label>
          <label>Дата на определяне<input type="date" required max={today} value={current.assessed_on} onChange={e => update({ ...current, assessed_on: e.target.value })}/></label>
          <label>Уред · по желание<input maxLength={80} value={current.device} onChange={e => update({ ...current, device: e.target.value })}/></label>
        </div>
        {current.source === "TEST" && <>
          <label>Протокол на лактатния тест<textarea required maxLength={250} placeholder="Продължителност на стъпалата, почивки и момент на пробата" value={current.protocol} onChange={e => update({ ...current, protocol: e.target.value })}/></label>
          <p>Въведи поне три стъпала с нарастващ пулс или скорост. Скоростта позволява ориентир за конкретното темпо, включително без пулс. Стойности извън измерения диапазон не се екстраполират. Това не определя автоматично физиологични прагове.</p>
          <div className="management-table-wrap"><table><thead><tr><th>Стъпало</th><th>Минути</th><th>Пулс</th><th>km/h · по желание</th><th>mmol/L</th><th/></tr></thead><tbody>{current.stages.map((s, i) => <tr key={i}><th>{i + 1}</th>{(["duration_min", "hr_bpm", "speed_kmh", "lactate_mmol"] as const).map(key => <td key={key}><input aria-label={`${i + 1}. ${key === "duration_min" ? "Продължителност" : key === "hr_bpm" ? "Пулс" : key === "speed_kmh" ? "Скорост" : "Лактат"}`} type="number" step={key === "hr_bpm" ? "1" : ".1"} min=".1" max={key === "hr_bpm" ? 250 : key === "duration_min" ? 60 : key === "speed_kmh" ? 150 : 40} required={key !== "speed_kmh" && key !== "hr_bpm"} value={s[key] || ""} onChange={e => update({ ...current, stages: current.stages.map((v, j) => j === i ? { ...v, [key]: (key === "speed_kmh" || key === "hr_bpm") ? optional(e.target.value) : Number(e.target.value) } : v) })}/></td>)}<td><button type="button" aria-label={`Премахни стъпало ${i + 1}`} disabled={current.stages.length <= 3} onClick={() => update({ ...current, stages: current.stages.filter((_, j) => j !== i) })}>×</button></td></tr>)}</tbody></table></div>
          <button type="button" className="action-button secondary" disabled={current.stages.length >= 30} onClick={() => update({ ...current, stages: [...current.stages, stage()] })}>Добави стъпало</button>
        </>}
        <p>{current.source === "TEST" ? "По желание въведи интерпретираните от треньора зонови граници; те имат предимство пред оценката по теста." : "Попълни наличните лични ориентири. Може да зададеш и само горна или долна граница."}</p>
        <div className="management-table-wrap"><table><thead><tr><th>Зона</th><th>От · mmol/L</th><th>До · mmol/L</th></tr></thead><tbody>{LACTATE_ZONES.map(zone => <tr key={zone}><th>{zone}</th>{(["low_mmol", "high_mmol"] as const).map(key => <td key={key}><input aria-label={`${zone} лактат ${key === "low_mmol" ? "от" : "до"}`} type="number" min={key === "low_mmol" ? "0" : ".1"} max="40" step=".1" value={current.zone_ranges[zone]?.[key] ?? ""} onChange={e => {
          const ranges = { ...current.zone_ranges }, range = { low_mmol: null, high_mmol: null, ...ranges[zone], [key]: optional(e.target.value) };
          if (range.low_mmol === null && range.high_mmol === null) delete ranges[zone]; else ranges[zone] = range;
          update({ ...current, zone_ranges: ranges });
        }}/></td>)}</tr>)}</tbody></table></div>
        <label>Условия и бележка<textarea maxLength={500} value={current.note} onChange={e => update({ ...current, note: e.target.value })}/></label>
        <button type="button" className="action-button secondary" onClick={() => update(null)}>Премахни текущия лактатен профил за {sports[sport]}</button>
      </>}
      <details><summary>Общи ориентири, когато няма лични данни</summary><p>Olympiatoppen 2024: I-1 до 1,5; I-2 приблизително 1–2; I-3 приблизително 1,5–3,5 mmol/L. Използват се като общ контекст за съответното ниво на усилие и не прекалибрират личните пулсови зони. За Z4/Z5 се използват индивидуални данни и конкретният метод.</p><a href="https://olt-skala.nif.no/en" target="_blank" rel="noreferrer">Източник и условия за прилагане</a></details>
    </details>
    <details className="training-observation-settings"><summary>Кратки ускорения и спринтове · нервно-мускулна работа</summary>
      <label className="management-check"><input type="checkbox" checked={nms.enabled} onChange={e => updateNms({ enabled: e.target.checked })}/>Включвай в подходящите леки аеробни тренировки</label>
      <p>Началните настройки са треньорска опора. Повторенията се добавят след загряване и упражнения, с пълни почивки, преди останалата аеробна работа.</p>
      {nms.enabled && <><div className="management-form-grid">
        <label>Вид отсечки<select value={nms.mode} onChange={e => updateNms({ mode: e.target.value as typeof nms.mode, work_seconds: e.target.value === "SHORT_SPRINT" ? Math.min(10, nms.work_seconds) : nms.work_seconds })}><option value="PROGRESSIVE_FINISH">Ускорения с максимален завършек</option><option value="SHORT_SPRINT">Кратки спринтове</option></select></label>
        <label>Повторения<input type="number" min="2" max="20" value={nms.repetitions} onChange={e => updateNms({ repetitions: Number(e.target.value) })}/></label>
        <label>Една отсечка · секунди<input type="number" min="5" max={nms.mode === "SHORT_SPRINT" ? 10 : 20} value={nms.work_seconds} onChange={e => updateNms({ work_seconds: Number(e.target.value) })}/></label>
        <label>Почивка след всяка отсечка · секунди<input type="number" min={Math.max(30, 4 * nms.work_seconds)} max="600" value={nms.recovery_seconds} onChange={e => updateNms({ recovery_seconds: Number(e.target.value) })}/></label>
      </div><p>Предпочитани дни:</p><div className="management-form-grid">{days.map((label, i) => <label className="management-check" key={i}><input type="checkbox" checked={nms.days.includes(i)} onChange={e => updateNms({ days: e.target.checked ? [...nms.days, i].sort() : nms.days.filter(d => d !== i) })}/>{label}</label>)}</div></>}
      <p>Отчитаме отделно повторенията и секундите NMS. Общото време включва и почивките. Q/E и Recovery още не оценяват пълния товар на ускоренията; избраният ден не гарантира добавка, ако няма подходяща сесия или време.</p>
    </details>
  </>;
}
