"use client";
import type { AnTReference } from "../lib/training-management";
import { PreferredSpeed } from "./running-speed-unit";

const sports = { Run: "Бягане", NordicSki: "Ски бягане", RollerSki: "Ролкови ски" };
export function AnTReferenceEditor({ value = [], onChange, today }: { value?: AnTReference[]; onChange: (value: AnTReference[]) => void; today: string }) {
  const update = (index: number, patch: Partial<AnTReference>) => onChange(value.map((r, i) => i === index ? {...r, ...patch} : r));
  return <details className="management-detail"><summary>AnT · измерени или ръчно зададени скоростни опори</summary>
    <p>Посочената стойност има предимство пред работната оценка 0,95 × CS. Оценката не е измерен лактатен праг и не презаписва тези данни.</p>
    {value.map((r, index) => <section className="management-panel" key={r.sport}><h4>{sports[r.sport]}</h4><div className="management-form-grid">
      <label>AnT скорост, км/ч<input type="number" min="0.001" max="80" step="any" value={r.speed_kmh || ""} onChange={e => update(index, {speed_kmh: Number(e.target.value)})} required/></label>
      <label>Произход<select value={r.source} onChange={e => update(index, {source: e.target.value as AnTReference["source"]})}><option value="MANUAL">Ръчно зададена опора</option><option value="MEASURED">Измерена стойност</option></select></label>
      <label>Дата на оценката<input type="date" max={today} value={r.assessed_on ?? ""} onChange={e => update(index, {assessed_on: e.target.value || null})}/></label>
    </div><p>Опора: <PreferredSpeed speed={r.speed_kmh} sport={r.sport}/></p>
      <label>Метод на измерване и условия<input maxLength={400} value={r.notes ?? ""} onChange={e => update(index, {notes: e.target.value})}/></label>
      <button type="button" className="action-button secondary" onClick={() => onChange(value.filter((_, i) => i !== index))}>Премахни AnT опората за {sports[r.sport]}</button>
    </section>)}
    <div className="management-actions">{(Object.keys(sports) as AnTReference["sport"][]).filter(s => !value.some(r => r.sport === s)).map(sport => <button key={sport} type="button" className="action-button secondary" onClick={() => onChange([...value, {sport, speed_kmh: 0, source: "MANUAL", assessed_on: today, notes: ""}])}>Добави AnT · {sports[sport]}</button>)}</div>
  </details>;
}
