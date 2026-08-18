import { exactKeys, finite, isCalendarDate, isRecord } from "./training-status";

export const WEEKDAYS = [
  "Понеделник",
  "Вторник",
  "Сряда",
  "Четвъртък",
  "Петък",
  "Събота",
  "Неделя",
] as const;

export interface PlanningProfile {
  schema_version: "planning-profile-v1";
  season_start: string;
  season_end: string;
  annual_target_hours: number;
  sessions_per_week: number;
  rest_days: number[];
  double_session_days: number[];
  long_session_day: number;
  intensity_days: number[];
  strength_days: number[];
  max_key_sessions_per_week: number;
  mesocycle_anchor_date: string;
  mesocycle_length_weeks: number;
  camp_default_accent_limit: number;
  double_threshold_enabled: boolean;
  double_threshold_day: number;
  double_threshold_components: Array<"Z3" | "Z4">;
}

export interface PlanningProfileResponse {
  configured: boolean;
  profile: PlanningProfile | null;
}

export interface PlanningMethodology {
  schema_version: "planning-methodology-v1";
  methodology_id: "onflows-canonical";
  methodology_version: "onflows-canonical-v1";
  source_scope: "BUILT_IN";
  mesocycle_pattern: number[];
  supported_accent_modes: Array<"AUTO" | "MANUAL" | "HYBRID">;
  accent_components: Array<"Z1" | "Z2" | "Z3" | "Z4" | "Z5" | "STR">;
  default_accent_limit: number;
  maximum_accent_limit: number;
  hybrid_rule: "manual-first-auto-fill";
  stress_mesocycle: {
    status: "DESIGNED_NOT_ACTIVE";
    automatic_enabled: false;
    manual_dose_required: true;
    selected_accents_only: true;
    mandatory_recovery: true;
    affects_canonical_result: false;
  };
}

export const MESOCYCLE_ACCENT_COMPONENTS = ["Z1", "Z2", "Z3", "Z4", "Z5", "STR"] as const;
export type MesocycleAccentComponent = typeof MESOCYCLE_ACCENT_COMPONENTS[number];
export type MesocycleAccentMode = "AUTO" | "MANUAL" | "HYBRID";

export interface MesocycleAccentPreferences {
  schema_version: "mesocycle-accent-preferences-v1";
  accent_mode: MesocycleAccentMode;
  accent_limit: number;
  manual_components: MesocycleAccentComponent[];
}

export interface MesocycleAccentPreferencesResponse {
  configured: boolean;
  preferences: MesocycleAccentPreferences | null;
  resolution: {
    methodology_version: "onflows-canonical-v1";
    fixed_components: MesocycleAccentComponent[];
    automatic_slots: number;
    resolution_stage: "PLAN_GENERATION";
  } | null;
}

const responseKeys = ["configured", "profile"];
const profileKeys = [
  "schema_version",
  "season_start",
  "season_end",
  "annual_target_hours",
  "sessions_per_week",
  "rest_days",
  "double_session_days",
  "long_session_day",
  "intensity_days",
  "strength_days",
  "max_key_sessions_per_week",
  "mesocycle_anchor_date",
  "mesocycle_length_weeks",
  "camp_default_accent_limit",
  "double_threshold_enabled",
  "double_threshold_day",
  "double_threshold_components",
];
const methodologyKeys = [
  "schema_version",
  "methodology_id",
  "methodology_version",
  "source_scope",
  "mesocycle_pattern",
  "supported_accent_modes",
  "accent_components",
  "default_accent_limit",
  "maximum_accent_limit",
  "hybrid_rule",
  "stress_mesocycle",
];
const stressKeys = [
  "status",
  "automatic_enabled",
  "manual_dose_required",
  "selected_accents_only",
  "mandatory_recovery",
  "affects_canonical_result",
];
const accentResponseKeys = ["configured", "preferences", "resolution"];
const accentPreferenceKeys = [
  "schema_version",
  "accent_mode",
  "accent_limit",
  "manual_components",
];
const accentResolutionKeys = [
  "methodology_version",
  "fixed_components",
  "automatic_slots",
  "resolution_stage",
];
const integer = (value: unknown, minimum: number, maximum: number): value is number =>
  Number.isInteger(value) && Number(value) >= minimum && Number(value) <= maximum;
const weekdays = (value: unknown): value is number[] =>
  Array.isArray(value)
  && value.every((day) => integer(day, 0, 6))
  && new Set(value).size === value.length;

export function parsePlanningProfileResponse(value: unknown): PlanningProfileResponse {
  if (!isRecord(value) || !exactKeys(value, responseKeys) || typeof value.configured !== "boolean")
    throw new Error("Невалидна структура на профила за планиране.");
  if (!value.configured) {
    if (value.profile !== null) throw new Error("Неконфигуриран planning profile съдържа стойности.");
    return { configured: false, profile: null };
  }
  const profile = value.profile;
  if (!isRecord(profile) || !exactKeys(profile, profileKeys))
    throw new Error("Невалидна структура на профила за планиране.");
  if (
    profile.schema_version !== "planning-profile-v1"
    || !isCalendarDate(profile.season_start)
    || !isCalendarDate(profile.season_end)
    || profile.season_end <= profile.season_start
    || !finite(profile.annual_target_hours)
    || profile.annual_target_hours < 50
    || profile.annual_target_hours > 1500
    || !integer(profile.sessions_per_week, 1, 14)
    || !weekdays(profile.rest_days)
    || profile.rest_days.length >= 7
    || profile.sessions_per_week > 2 * (7 - profile.rest_days.length)
    || !weekdays(profile.double_session_days)
    || !integer(profile.long_session_day, 0, 6)
    || !weekdays(profile.intensity_days)
    || !weekdays(profile.strength_days)
    || !integer(profile.max_key_sessions_per_week, 0, 8)
    || !isCalendarDate(profile.mesocycle_anchor_date)
    || !integer(profile.mesocycle_length_weeks, 2, 6)
    || !integer(profile.camp_default_accent_limit, 1, 6)
    || typeof profile.double_threshold_enabled !== "boolean"
    || !integer(profile.double_threshold_day, 0, 6)
    || !Array.isArray(profile.double_threshold_components)
    || profile.double_threshold_components.length === 0
    || new Set(profile.double_threshold_components).size !== profile.double_threshold_components.length
    || !profile.double_threshold_components.every((component) => component === "Z3" || component === "Z4")
    || (profile.double_threshold_enabled && profile.rest_days.includes(profile.double_threshold_day))
    || (profile.double_threshold_enabled && profile.max_key_sessions_per_week < 2)
  ) throw new Error("Невалидни стойности в профила за планиране.");
  return { configured: true, profile: profile as unknown as PlanningProfile };
}

export function parsePlanningMethodology(value: unknown): PlanningMethodology {
  const methodology = value;
  if (
    !isRecord(methodology)
    || !exactKeys(methodology, methodologyKeys)
    || methodology.schema_version !== "planning-methodology-v1"
    || methodology.methodology_id !== "onflows-canonical"
    || methodology.methodology_version !== "onflows-canonical-v1"
    || methodology.source_scope !== "BUILT_IN"
    || !Array.isArray(methodology.mesocycle_pattern)
    || methodology.mesocycle_pattern.length !== 4
    || !methodology.mesocycle_pattern.every(finite)
    || !Array.isArray(methodology.supported_accent_modes)
    || methodology.supported_accent_modes.join(",") !== "AUTO,MANUAL,HYBRID"
    || !Array.isArray(methodology.accent_components)
    || methodology.accent_components.join(",") !== "Z1,Z2,Z3,Z4,Z5,STR"
    || methodology.default_accent_limit !== 2
    || methodology.maximum_accent_limit !== 6
    || methodology.hybrid_rule !== "manual-first-auto-fill"
    || !isRecord(methodology.stress_mesocycle)
    || !exactKeys(methodology.stress_mesocycle, stressKeys)
    || methodology.stress_mesocycle.status !== "DESIGNED_NOT_ACTIVE"
    || methodology.stress_mesocycle.automatic_enabled !== false
    || methodology.stress_mesocycle.manual_dose_required !== true
    || methodology.stress_mesocycle.selected_accents_only !== true
    || methodology.stress_mesocycle.mandatory_recovery !== true
    || methodology.stress_mesocycle.affects_canonical_result !== false
  ) throw new Error("Невалидна versioned методология за планиране.");
  return methodology as unknown as PlanningMethodology;
}

const accentComponents = (value: unknown): value is MesocycleAccentComponent[] => {
  if (!Array.isArray(value) || new Set(value).size !== value.length) return false;
  const positions = value.map((component) =>
    MESOCYCLE_ACCENT_COMPONENTS.indexOf(component as MesocycleAccentComponent));
  return positions.every((position, index) =>
    position >= 0 && (index === 0 || position > positions[index - 1]));
};

export function parseMesocycleAccentPreferencesResponse(
  value: unknown,
): MesocycleAccentPreferencesResponse {
  if (!isRecord(value) || !exactKeys(value, accentResponseKeys) || typeof value.configured !== "boolean")
    throw new Error("Невалидна структура на мезоцикличните акценти.");
  if (!value.configured) {
    if (value.preferences !== null || value.resolution !== null)
      throw new Error("Неконфигурирани мезоциклични акценти съдържат стойности.");
    return { configured: false, preferences: null, resolution: null };
  }
  const preferences = value.preferences;
  const resolution = value.resolution;
  if (
    !isRecord(preferences)
    || !exactKeys(preferences, accentPreferenceKeys)
    || preferences.schema_version !== "mesocycle-accent-preferences-v1"
    || !["AUTO", "MANUAL", "HYBRID"].includes(String(preferences.accent_mode))
    || !integer(preferences.accent_limit, 1, MESOCYCLE_ACCENT_COMPONENTS.length)
    || !accentComponents(preferences.manual_components)
    || preferences.manual_components.length > preferences.accent_limit
    || (preferences.accent_mode === "AUTO" && preferences.manual_components.length !== 0)
    || (preferences.accent_mode !== "AUTO" && preferences.manual_components.length === 0)
    || !isRecord(resolution)
    || !exactKeys(resolution, accentResolutionKeys)
    || resolution.methodology_version !== "onflows-canonical-v1"
    || !accentComponents(resolution.fixed_components)
    || !integer(resolution.automatic_slots, 0, MESOCYCLE_ACCENT_COMPONENTS.length)
    || resolution.resolution_stage !== "PLAN_GENERATION"
  ) throw new Error("Невалидни стойности на мезоцикличните акценти.");
  const expectedFixed = preferences.accent_mode === "AUTO" ? [] : preferences.manual_components;
  const expectedAutomaticSlots = preferences.accent_mode === "AUTO"
    ? preferences.accent_limit
    : preferences.accent_mode === "MANUAL"
      ? 0
      : preferences.accent_limit - preferences.manual_components.length;
  if (
    resolution.fixed_components.join(",") !== expectedFixed.join(",")
    || resolution.automatic_slots !== expectedAutomaticSlots
  ) throw new Error("Несъгласувано разрешаване на мезоцикличните акценти.");
  return value as unknown as MesocycleAccentPreferencesResponse;
}
