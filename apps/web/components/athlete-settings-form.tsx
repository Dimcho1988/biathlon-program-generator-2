"use client";

import { useState } from "react";

const boundaryFields = [
  ["z1_low", "Начало Z1"],
  ["z2_low", "Начало Z2"],
  ["z3_low", "Начало Z3"],
  ["z4_low", "Начало Z4"],
  ["z5_low", "Начало Z5"],
  ["z5_high", "Край Z5"],
] as const;

type AthleteSettingsFormProps = {
  notice?: string;
  initialBounds?: [number, number, number, number, number, number] | null;
  initialTimezone?: string | null;
  initialHrmax?: number | null;
  initialSource?: "MANUAL" | "AUTOMATIC_HRMAX" | null;
  initialPercentages?: number[] | null;
  automaticPercentages?: number[] | null;
  editing?: boolean;
};

export function AthleteSettingsForm({
  notice,
  initialBounds = null,
  initialTimezone = null,
  initialHrmax = null,
  initialSource = null,
  initialPercentages = null,
  automaticPercentages = null,
  editing = false,
}: AthleteSettingsFormProps) {
  const percentages = initialSource === "AUTOMATIC_HRMAX" ? initialPercentages : automaticPercentages;
  const autoAvailable = percentages?.length === 6;
  const [source, setSource] = useState<"MANUAL" | "AUTOMATIC_HRMAX">(
    initialSource ?? (!initialBounds && autoAvailable ? "AUTOMATIC_HRMAX" : "MANUAL")
  );
  const [hrmax, setHrmax] = useState(initialHrmax?.toString() ?? "");
  const automaticBounds = percentages?.map(percent => Math.floor(Number(hrmax) * percent / 100 + 0.5));
  const validPreview = hrmax !== "" && Number(hrmax) >= 30 && Number(hrmax) <= 240
    && automaticBounds?.every((bound, index) => bound >= 30 && (index === 0 || automaticBounds[index - 1] < bound));
  return (
    <main className="state-page settings-page">
      <p className="eyebrow">Индивидуална конфигурация</p>
      <h1>{editing ? "Настройки на профила" : "Настройте пулсовите зони"}</h1>
      <p className="muted">
        {editing
          ? "Новите граници ще се използват при следващото обновяване на тренировките."
          : "Задайте своя максимален пулс и начина за определяне на зоните."}
      </p>
      {notice && <p className="connection-notice">{notice}</p>}
      <form className="athlete-settings-form" action="/api/athlete/settings" method="post">
        <label className="timezone-field">
          <span>Максимален пулс (уд/мин)</span>
          <input name="hrmax_bpm" type="number" min="30" max="240" required inputMode="numeric" value={hrmax} onChange={event => setHrmax(event.target.value)} />
          <small>Въведете известния HRmax. Не го изчисляваме от възрастта или от една тренировка.</small>
        </label>
        {autoAvailable ? <label className="timezone-field">
          <span>Пулсови зони</span>
          <select name="hr_zone_source" value={source} onChange={event => setSource(event.target.value as typeof source)}>
            <option value="AUTOMATIC_HRMAX">Ориентировъчни по максимален пулс</option>
            <option value="MANUAL">Мои индивидуални граници</option>
          </select>
        </label> : <input type="hidden" name="hr_zone_source" value="MANUAL" />}
        {source === "AUTOMATIC_HRMAX" && <div className="state-help">
          <p>Начална оценка по експертна схема. Можете да я замените с индивидуални граници.</p>
          {validPreview && <p aria-live="polite">Граници: {automaticBounds!.join(" / ")} уд/мин.</p>}
        </div>}
        <fieldset disabled={source === "AUTOMATIC_HRMAX"} hidden={source === "AUTOMATIC_HRMAX"}>
          <legend>HR граници (уд/мин)</legend>
          <div className="boundary-grid">
            {boundaryFields.map(([name, label], index) => (
              <label key={name}>
                <span>{label}</span>
                <input name={name} type="number" min="30" max="240" required inputMode="numeric" defaultValue={initialBounds?.[index]} />
              </label>
            ))}
          </div>
        </fieldset>
        {!autoAvailable && <p className="state-help">Автоматичните зони ще бъдат достъпни след задаване на експертната схема. Засега въведете индивидуалните си граници.</p>}
        <label className="timezone-field">
          <span>Часова зона</span>
          <input name="timezone" type="text" defaultValue={initialTimezone ?? "Europe/Sofia"} maxLength={64} required autoComplete="off" />
        </label>
        <button className="action-button" type="submit">Запази настройките</button>
      </form>
      <p className="state-help">Версиите на Tref, intra-zone и recovery модела остават общи и одобрени за всички профили.</p>
    </main>
  );
}
