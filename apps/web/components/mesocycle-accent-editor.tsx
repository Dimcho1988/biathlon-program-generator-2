"use client";

import { useState } from "react";
import {
  MESOCYCLE_ACCENT_COMPONENTS,
  type MesocycleAccentComponent,
  type MesocycleAccentMode,
  type MesocycleAccentPreferencesResponse,
  type PlanningMethodology,
} from "../lib/planning-profile";

const MODE_LABELS: Record<MesocycleAccentMode, string> = {
  AUTO: "Автоматично",
  MANUAL: "Ръчно",
  HYBRID: "Хибридно",
};

export function MesocycleAccentEditor({
  response,
  methodology,
  profileConfigured,
}: {
  response: MesocycleAccentPreferencesResponse;
  methodology: PlanningMethodology;
  profileConfigured: boolean;
}) {
  const stored = response.preferences;
  const [mode, setMode] = useState<MesocycleAccentMode>(
    stored?.accent_mode ?? "AUTO",
  );
  const [limit, setLimit] = useState(
    stored?.accent_limit ?? methodology.default_accent_limit,
  );
  const [manualComponents, setManualComponents] = useState<MesocycleAccentComponent[]>(
    stored?.manual_components ?? [],
  );

  const fixedComponents = mode === "AUTO" ? [] : manualComponents;
  const automaticSlots = mode === "AUTO"
    ? limit
    : mode === "MANUAL"
      ? 0
      : limit - fixedComponents.length;
  const validSelection = mode === "AUTO" || fixedComponents.length > 0;

  const changeLimit = (next: number) => {
    setLimit(next);
    setManualComponents((current) => current.slice(0, next));
  };
  const toggleComponent = (component: MesocycleAccentComponent) => {
    setManualComponents((current) => current.includes(component)
      ? current.filter((value) => value !== component)
      : MESOCYCLE_ACCENT_COMPONENTS.filter((value) =>
        value === component || current.includes(value)).slice(0, limit));
  };

  return <section className="accent-editor-card" aria-labelledby="accent-editor-title">
    <div className="accent-editor-heading">
      <div>
        <p className="eyebrow">Индивидуално правило</p>
        <h2 id="accent-editor-title">Акценти на мезоцикъла</h2>
      </div>
      <span className={`configuration-badge ${response.configured ? "configured" : ""}`}>
        {response.configured ? "Запазено" : "Не е конфигурирано"}
      </span>
    </div>
    <p className="muted">
      Избира се само начинът на разпределение. Автоматичните компоненти се
      определят от периодизацията при генериране на плана, когато има календарен контекст.
    </p>
    {!profileConfigured && <p className="planning-unconfigured">
      Първо запази основния профил за планиране.
    </p>}
    <form className="accent-editor-form" action="/api/athlete/mesocycle-accents" method="post">
      <input type="hidden" name="schema_version" value="mesocycle-accent-preferences-v1" />
      <div className="planning-grid two-columns">
        <label>
          <span>Режим</span>
          <select
            name="accent_mode"
            value={mode}
            onChange={(event) => {
              const next = event.target.value as MesocycleAccentMode;
              setMode(next);
              if (next === "AUTO") setManualComponents([]);
            }}
            disabled={!profileConfigured}
          >
            {methodology.supported_accent_modes.map((value) =>
              <option key={value} value={value}>{MODE_LABELS[value]}</option>)}
          </select>
        </label>
        <label>
          <span>Максимален брой акценти</span>
          <select
            name="accent_limit"
            value={limit}
            onChange={(event) => changeLimit(Number(event.target.value))}
            disabled={!profileConfigured}
          >
            {Array.from({ length: methodology.maximum_accent_limit }, (_, index) => index + 1)
              .map((value) => <option key={value} value={value}>{value}</option>)}
          </select>
        </label>
      </div>
      <fieldset className="accent-component-fieldset" disabled={!profileConfigured || mode === "AUTO"}>
        <legend>Ръчно избрани компоненти</legend>
        <div className="component-choices accent-component-choices">
          {MESOCYCLE_ACCENT_COMPONENTS.map((component) => {
            const checked = manualComponents.includes(component);
            return <label key={component}>
              <input
                name="manual_components"
                type="checkbox"
                value={component}
                checked={checked}
                disabled={
                  !profileConfigured
                  || mode === "AUTO"
                  || (!checked && manualComponents.length >= limit)
                }
                onChange={() => toggleComponent(component)}
              />
              <span>{component}</span>
            </label>;
          })}
        </div>
      </fieldset>
      <div className="accent-resolution-preview" aria-live="polite">
        <span className="accent-preview-label">Предварително разрешаване</span>
        <div className="accent-preview-slots">
          {fixedComponents.map((component) =>
            <span className="accent-slot fixed" key={component}>{component} · ръчно</span>)}
          {Array.from({ length: automaticSlots }, (_, index) =>
            <span className="accent-slot automatic" key={`auto-${index}`}>
              AUTO {index + 1}
            </span>)}
          {!fixedComponents.length && !automaticSlots && <span className="muted">Няма избран акцент.</span>}
        </div>
        <p>
          {mode === "AUTO"
            ? `${limit} позиции ще бъдат определени динамично.`
            : mode === "MANUAL"
              ? "Само избраните компоненти са фиксирани; лимитът е максимален, не задължителен брой."
              : `${fixedComponents.length} ръчни и ${automaticSlots} автоматични позиции.`}
        </p>
      </div>
      {!validSelection && <p className="accent-validation" role="alert">
        При MANUAL и HYBRID избери поне един ръчен компонент.
      </p>}
      <button
        className="action-button"
        type="submit"
        disabled={!profileConfigured || !validSelection}
      >
        Запази правилото за акцентите
      </button>
    </form>
    <p className="methodology-note">
      Настройката не променя текущия анализ. Тя ще стане вход към versioned планов
      snapshot при интегрирането на генератора. STRESS остава неактивен.
    </p>
  </section>;
}
