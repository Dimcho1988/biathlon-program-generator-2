"use client";
import type { RaceCourse } from "../lib/planning-calendar";

export function RaceCourseEditor({ value, onChange }: { value?: RaceCourse; onChange: (value?: RaceCourse) => void }) {
  const set = (patch: Partial<RaceCourse>) => value && onChange({ ...value, ...patch });
  const pair = (key: "gradient_percent" | "expected_duration_min", label: string) => <fieldset>
    <legend>{label}</legend><div className="planning-grid calendar-columns">{[0, 1].map(index => <label key={index}>{index ? "До" : "От"}<input
      type="number" step="0.1" min="0.1" max={key === "gradient_percent" ? 60 : 1440}
      aria-label={`${label} ${index ? "до" : "от"}`} value={value?.[key]?.[index] ?? ""}
      onChange={event => { const bounds = [...(value?.[key] ?? [0, 0])] as [number, number]; bounds[index] = Number(event.target.value); set({ [key]: bounds.every(n => n === 0) ? undefined : bounds }); }}/></label>)}</div>
  </fieldset>;
  return <div className="race-course-editor">
    <label><input type="checkbox" checked={!!value} onChange={event => onChange(event.target.checked
      ? { distance_m: 0, surface: "CROSS", profile: "ROLLING", specific_sessions_percent: 70, familiar_terrain: false } : undefined)}/> Задай дисциплина и условия за този старт</label>
    {value && <>
      <div className="planning-grid calendar-columns">
        <label>Дистанция (м)<input type="number" min="50" max="1000000" step="1" required value={value.distance_m || ""} onChange={event => set({ distance_m: Number(event.target.value) })}/></label>
        <label>Настилка<select value={value.surface} onChange={event => { const surface = event.target.value as RaceCourse["surface"]; set({ surface, ...(surface === "TRACK" ? { profile: "FLAT", gradient_percent: undefined } : {}) }); }}>
          <option value="TRACK">Писта</option><option value="ROAD">Шосе</option><option value="CROSS">Крос</option><option value="TRAIL">Планинско / трейл</option><option value="SNOW">Сняг</option>
        </select></label>
        <label>Профил на трасето<select value={value.profile} disabled={value.surface === "TRACK"} onChange={event => { const profile = event.target.value as RaceCourse["profile"]; set({ profile, ...(profile !== "UPHILL" ? { gradient_percent: undefined } : {}) }); }}>
          <option value="FLAT">Равно</option><option value="UPHILL">Само изкачване</option><option value="ROLLING">Изкачвания и спускания</option>
        </select></label>
      </div>
      {value.profile === "UPHILL" && pair("gradient_percent", "Наклон (%)")}
      <div className="planning-grid calendar-columns">{(["ascent_m", "descent_m"] as const).map(key => <label key={key}>{key === "ascent_m" ? "Общо изкачване (м)" : "Общо спускане (м)"}<input type="number" step="1" min="0" max="100000" value={value[key] ?? ""} onChange={event => set({ [key]: event.target.value === "" ? undefined : Number(event.target.value) })}/></label>)}</div>
      {pair("expected_duration_min", "Очаквана продължителност (мин)")}
      <p className="management-muted">По желание: посочи двете граници за конкретното трасе. Те насочват продължителността на моделните отсечки. При липса се използва наличната индивидуална оценка за дистанцията.</p>
      <label>Дял от подходящите основни тренировки в специфични условия (%)<input type="number" min="0" max="100" step="1" value={value.specific_sessions_percent ?? 70} onChange={event => set({ specific_sessions_percent: Number(event.target.value) })}/></label>
      <p className="management-muted">Работна треньорска настройка. Моделните занимания са с предимство; делът се закръгля според броя подходящи сесии в проекта. 0% изключва автоматичните указания за терена.</p>
      <label><input type="checkbox" checked={value.familiar_terrain ?? false} onChange={event => set({ familiar_terrain: event.target.checked })}/> Атлетът е подготвен за тези условия — могат да се използват и в тейпъра</label>
      <p className="management-muted">По наклон и променлив терен водят времето и усилието. Изчислените от равнинно темпо метри са еквивалент, а не задължителна дистанция.</p>
    </>}
  </div>;
}
