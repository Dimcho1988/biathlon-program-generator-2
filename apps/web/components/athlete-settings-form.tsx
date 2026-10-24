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
  initialAge?: number | null;
  initialIndicator?: "HEART_RATE" | "SPEED";
  initialHrmaxSource?: "MANUAL" | "TANAKA" | "OBSERVED" | null;
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
  initialAge = null,
  initialIndicator = "HEART_RATE",
  initialHrmaxSource = null,
}: AthleteSettingsFormProps) {
  const percentages = initialSource === "AUTOMATIC_HRMAX" ? initialPercentages : automaticPercentages;
  const autoAvailable = percentages?.length === 6;
  const [source, setSource] = useState<"MANUAL" | "AUTOMATIC_HRMAX">(
    initialSource ?? (!initialBounds && autoAvailable ? "AUTOMATIC_HRMAX" : "MANUAL")
  );
  const [hrmax, setHrmax] = useState(initialHrmaxSource === "TANAKA" ? "" : initialHrmax?.toString() ?? "");
  const [age, setAge] = useState(initialAge?.toString() ?? "");
  const predictedMaximum = age !== "" ? Math.floor(208 - 0.7 * Number(age) + 0.5) : null;
  const maximum = hrmax !== "" ? Number(hrmax) : predictedMaximum;
  const automaticBounds = maximum ? percentages?.map(percent => Math.floor(maximum * percent / 100 + 0.5)) : null;
  const validPreview = maximum !== null && maximum >= 30 && maximum <= 240
    && automaticBounds?.every((bound, index) => bound >= 30 && (index === 0 || automaticBounds[index - 1] < bound));
  return (
    <main className="state-page settings-page">
      <p className="eyebrow">Индивидуална конфигурация</p>
      <h1>{editing ? "Настройки на профила" : "Настройте пулсовите зони"}</h1>
      <p className="muted">
        {editing
          ? "Водещият показател се използва за натоварването, 7/40 и възстановяването."
          : "Задайте своя максимален пулс и начина за определяне на зоните."}
      </p>
      {notice && <p className="connection-notice">{notice}</p>}
      <form className="athlete-settings-form" action="/api/athlete/settings" method="post">
        <label className="timezone-field">
          <span>Максимален пулс (уд/мин)</span>
          <input name="hrmax_bpm" type="number" min="30" max="240" inputMode="numeric" value={hrmax} onChange={event => setHrmax(event.target.value)} />
          <small>Въведете известния HRmax или оставете празно за начална оценка по възрастта. По-висок устойчив записан пулс може автоматично да го уточни.</small>
          {initialHrmaxSource === "TANAKA" && <small>Текущият HRmax е начална оценка по Tanaka.</small>}
          {initialHrmaxSource === "OBSERVED" && <small>Текущият HRmax е уточнен от устойчив записан пулсов пик.</small>}
        </label>
        <label className="timezone-field">
          <span>Възраст (години)</span>
          <input name="age_years" type="number" min="5" max="100" inputMode="numeric" value={age} onChange={event => setAge(event.target.value)} required={hrmax === ""} />
          {hrmax === "" && predictedMaximum !== null && <small>Начален HRmax по Tanaka: {predictedMaximum} уд/мин.</small>}
        </label>
        <label className="timezone-field">
          <span>Водещ показател за натоварването</span>
          <select name="leading_indicator" defaultValue={initialIndicator}>
            <option value="HEART_RATE">Пулс</option>
            <option value="SPEED">Приравнена скорост (Vflat)</option>
          </select>
          <small>При липсващ водещ показател се използва другият, когато има необходимите данни за връзката между тях.</small>
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
                {index === 0 || index === 5 ? <input name={name} type="number" min="30" max="240" required inputMode="numeric" readOnly
                  value={maximum ? (index === 0 ? Math.floor(maximum * .45 + .5) : maximum) : ""} />
                  : <input name={name} type="number" min="30" max="240" required inputMode="numeric" defaultValue={initialBounds?.[index]} />}
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
      <p className="state-help">Началото на Z1 е 45% от максималния пулс. При колоездене работните пулсови граници са със 7 уд/мин по-ниски.</p>
    </main>
  );
}
