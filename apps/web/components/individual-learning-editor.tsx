"use client";

import { defaultIndividualLearning, LEARNING_MODE_LABELS, type LearningMode } from "../lib/individual-learning";
import { defaultLoadProgression, type ManagementProfile } from "../lib/training-management";

export function IndividualLearningEditor({ profile, onChange }: {
  profile: ManagementProfile; onChange: (profile: ManagementProfile) => void;
}) {
  const value = profile.individual_learning ?? defaultIndividualLearning();
  const progression = profile.load_progression ?? defaultLoadProgression();
  const update = (patch: Partial<typeof value>) => onChange({ ...profile, individual_learning: { ...value, ...patch } });
  return <details className="management-learning-settings">
    <summary>Самообучение · {LEARNING_MODE_LABELS[value.mode]}</summary>
    <p>Системата сравнява изпълнените тренировки с последващата реакция и уточнява предложенията за всеки компонент.</p>
    <label>Режим на самообучение<select value={value.mode} onChange={event => update({ mode: event.target.value as LearningMode })}>
      <option value="OFF">Изключено</option><option value="SHADOW">Наблюдение</option><option value="CONTROL">Управление</option>
    </select></label>
    <p className="management-muted">{value.mode === "OFF" ? "Самообучението не участва в плана."
      : value.mode === "SHADOW" ? "Натрупва наблюдения и показва предложения. Те не променят предписаното натоварване."
      : "Прилага допустимите предложения в плана според наличните данни и проверката на прогнозите. Готовността, периодизацията и зададените ограничения остават в сила."}</p>
    {value.mode !== "OFF" && (!progression.enabled || !progression.feedback_enabled) && <p className="management-notice">Самообучението е спряно от настройките по-горе. За да работи, включи управлението на прираста и адаптирането от завършени блокове.</p>}
    {value.mode !== "OFF" && <>
      <label className="management-check"><input type="checkbox" checked={value.exploration_enabled}
        onChange={event => update({ exploration_enabled: event.target.checked })}/>Допускай малки пробни промени</label>
      <p className="management-muted">След наблюдавано възстановяване малка пробна промяна може да помогне за натрупване на данни. Тя се отбелязва отделно и остава в избраните граници. При „Наблюдение“ се показва само като предложение.</p>
      <div className="management-form-grid">
        <label>Максимална стъпка на обема, %<input type="number" min="0" max="10" step=".5"
          value={value.max_volume_step_percent} onChange={event => update({ max_volume_step_percent: Number(event.target.value) })}/></label>
        <label>Максимална стъпка в зоната, п.п.<input type="number" min="0" max="5" step=".5"
          value={Number((value.max_intensity_step * 100).toFixed(2))} onChange={event => update({ max_intensity_step: Number(event.target.value) / 100 })}/></label>
      </div>
      <p className="management-muted">Позицията в зоната определя целевата интензивност в нейните граници. Две процентни пункта тук не означават 2% по-висока скорост.</p>
    </>}
  </details>;
}
