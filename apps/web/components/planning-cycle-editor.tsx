"use client";
import { COMPONENTS, type CycleDirective, type ManagementProfile } from "../lib/training-management";
import { CYCLE_LABELS, shiftDay } from "../lib/planning-timeline";

export function PlanningCycleEditor({ profile, onChange }: { profile: ManagementProfile; onChange: (p: ManagementProfile) => void }) {
  const controls = profile.planning_controls;
  if (!controls) return null;
  const update = (i: number, patch: Partial<CycleDirective>) => onChange({ ...profile, planning_controls: { ...controls, cycles: controls.cycles.map((c, j) => i === j ? { ...c, ...patch } : c) } });
  const end = profile.horizon_mode === "MANUAL" ? profile.program_end : shiftDay(profile.program_start, 365);
  return <section aria-label="Тренировъчни блокове в календара"><h3>Мои блокове и акценти</h3>{!controls.cycles.length && <p className="management-muted">Няма ръчни блокове. Следват се автоматичните акценти и вълната на натоварване.</p>}{controls.cycles.map((c, i) => <fieldset className="management-cycle" key={i}><legend>{c.name}</legend><div className="management-form-grid">
    <label>Име на блока<input maxLength={80} required value={c.name} onChange={e => update(i, { name: e.target.value })}/></label>
    <label>Тип блок<select value={c.kind} onChange={e => { const kind = e.target.value as CycleDirective["kind"]; update(i, { kind, target_index: kind === "RECOVERY" ? Math.min(1, c.target_index) : kind === "STRESS" ? Math.max(1.1, c.target_index) : c.target_index, volume_factor: kind === "RECOVERY" ? Math.min(1, c.volume_factor) : c.volume_factor }); }}>{Object.entries(CYCLE_LABELS).map(([k, label]) => <option key={k} value={k}>{label}</option>)}</select></label>
    <label>Блок от<input required type="date" min={profile.program_start} max={end} value={c.start_date} onChange={e => update(i, { start_date: e.target.value })}/></label><label>Блок до<input required type="date" min={c.start_date} max={end} value={c.end_date} onChange={e => update(i, { end_date: e.target.value })}/></label>
    <label>Целеви 7/40 за акцентите<input type="number" min="0.5" max={c.kind === "RECOVERY" ? "1" : "2"} step="0.01" value={c.target_index} onChange={e => update(i, { target_index: Number(e.target.value) })}/></label>
    {c.kind === "STRESS" && <label>Разтоварване след блока, дни<input type="number" min="7" max="14" value={c.recovery_days} onChange={e => update(i, { recovery_days: Number(e.target.value) })}/></label>}
  </div><div role="group" aria-label={`Акценти на ${c.name}`} className="management-zone-legend">{COMPONENTS.map(z => <label className="management-check" key={z}><input type="checkbox" checked={c.accents.includes(z)} onChange={e => update(i, { accents: e.target.checked ? [...c.accents, z] : c.accents.filter(v => v !== z) })}/>{z}</label>)}</div><button type="button" className="text-action" onClick={() => onChange({ ...profile, planning_controls: { ...controls, cycles: controls.cycles.filter((_, j) => j !== i) } })}>Премахни блока</button></fieldset>)}
    <p className="management-muted">Стресовият микроцикъл е до 7 дни, с 7–14 дни разтоварване след него. Останалите блокове са до 42 дни. Лагерът сам по себе си не увеличава товара.</p>
  </section>;
}
