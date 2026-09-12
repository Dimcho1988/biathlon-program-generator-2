"use client";

import { useState, useTransition, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { MODEL_ZONES, saveModel, type RecoveryV2, type ZoneConfig } from "../lib/models";

const parameters: Array<{ key: keyof ZoneConfig; label: string; min: number; max: number }> = [
  { key: "duration_coefficient", label: "Продължителност ×", min: .1, max: 5 },
  { key: "shape", label: "Стръмност", min: 1, max: 10 },
  { key: "sensitivity", label: "Чувствителност", min: .05, max: 3 },
  { key: "initial_daily_min", label: "Начална база, мин/ден", min: .1, max: 600 },
];

export function RecoverySettingsEditor({ history, canEdit }: { history: RecoveryV2; canEdit: boolean }) {
  const router = useRouter();
  const [draft, setDraft] = useState({ revision: history.config_revision, zones: history.settings });
  const [saving, setSaving] = useState(false);
  const [refreshing, startRefresh] = useTransition();
  const [savedRevision, setSavedRevision] = useState<number | null>(null);
  const [error, setError] = useState("");
  // Refresh the input values when a new server revision arrives without
  // remounting the section, its selected zones or its open details panels.
  if (draft.revision !== history.config_revision) {
    setDraft({ revision: history.config_revision, zones: history.settings });
  }
  const busy = saving || refreshing;
  const awaitingResult = savedRevision !== null && history.config_revision < savedRevision;
  const dirty = MODEL_ZONES.some(zone => parameters.some(({ key }) => draft.zones[zone][key] !== history.settings[zone][key]));
  const refresh = () => startRefresh(() => router.refresh());

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canEdit || busy || awaitingResult || !dirty) return;
    setSaving(true);
    setError("");
    try {
      const result = await saveModel("recovery", { expected_revision: draft.revision, zones: draft.zones });
      if (result.saved !== true || !Number.isInteger(result.revision) || result.revision <= draft.revision) {
        throw new Error("Сървърът не потвърди нова версия на настройките. Презаредете преди следващ запис.");
      }
      setSavedRevision(result.revision);
      refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Грешка при запис.");
    } finally {
      setSaving(false);
    }
  }

  const message = error || (saving ? "Записване на настройките…"
    : awaitingResult ? "Настройките са запазени. Изчакваме преизчислените карти и графика…"
    : dirty ? "Има незапазени промени. Картите и графиката ще се обновят след „Запази и преизчисли“."
    : savedRevision !== null ? "Настройките са приложени. Картите и графиката са преизчислени."
    : "Картите и графиката показват последните запазени настройки.");

  return <details className="recovery-settings"><summary>Донастройване по зони</summary>
    <form onSubmit={save}>
      <p>Продължителност × 1 е началният модел. Стръмност 1 е обикновена експонента; по-висока стойност засилва бързата начална фаза при същия срок до 90% за съществено натоварване. При малък товар близо до прага готовността може да настъпи по-рано.</p>
      <div className="activity-table-wrap"><table><thead><tr><th>Зона</th>{parameters.map(parameter => <th key={parameter.key}>{parameter.label}</th>)}</tr></thead>
        <tbody>{MODEL_ZONES.map(zone => <tr key={zone}><th>{zone}</th>{parameters.map(parameter => <td key={parameter.key}>
          <input type="number" required aria-label={`${zone} ${parameter.label}`} disabled={!canEdit || busy || awaitingResult}
            min={parameter.min} max={parameter.max} step="any" value={draft.zones[zone][parameter.key]}
            onChange={event => {
              setError("");
              setDraft({ ...draft, zones: { ...draft.zones, [zone]: { ...draft.zones[zone], [parameter.key]: Number(event.target.value) } } });
            }} style={{ width: "6rem" }} />
        </td>)}</tr>)}</tbody></table></div>
      <p>Чувствителността определя първоначалната умора. Началната база плавно отстъпва при поне 7 дни история и натрупан товар, равен на 7 начални дневни дози. При нулев или съвсем малък товар остава експертна опора.</p>
      <p>Началната база има значение при недостатъчна история. Когато се използва личната средна, промяната ѝ може да няма видим ефект. За по-кратко или по-дълго възстановяване променете „Продължителност ×“ за съответната зона.</p>
      {canEdit && <button type="submit" className="action-button" disabled={busy || awaitingResult || !dirty}>{busy ? "Записване и преизчисляване…" : "Запази и преизчисли"}</button>}
      <p role="status" aria-live="polite">{message}</p>
      {awaitingResult && !busy && <button type="button" className="action-button secondary" onClick={refresh}>Покажи преизчисления резултат</button>}
    </form>
  </details>;
}
