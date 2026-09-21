import { isCalendarDate, isRecord } from "./training-status";

export interface ManagementProfile {
  schema_version: "management-profile-v1";
  sport: "Run" | "NordicSki";
  actual_sport: "Run" | "NordicSki" | "RollerSki";
  discipline: string;
  age_years: number | null;
  training_experience_years: number | null;
  race_duration_min: number | null;
  program_start: string;
  program_end: string;
  available_minutes: number[];
  recent_weekly_hours: number[] | null;
  reentry_days: number | null;
  taper_days: number;
  max_key_sessions_per_week: number;
  building_fraction: number;
  maintenance_fraction: number;
  reentry_fraction: number;
  recovery_session_cap_min: number;
  allow_expert_fallback: boolean;
  adaptation_mode: "AUTO" | "REVIEW";
  auto_import_enabled: boolean;
  progression_percent: number;
  component_targets_weekly: Partial<Record<Component, number>>;
  interval_profiles: IntervalDoseProfile[];
  strength_enabled: boolean;
  strength_circuits: number;
  transition_days: number;
}

export interface IntervalDoseProfile {
  zone: "Z4" | "Z5"; sport: "Run" | "NordicSki" | "RollerSki";
  continuous_capacity_min: number; assessed_on: string; effort: string;
  work_seconds: number; recovery_seconds: number; min_repetitions: number; max_repetitions: number;
  total_capacity_ratio: number; reserve_repetitions: number; target_speed_kmh: number | null; speed_basis?: "ACTUAL" | "FLAT_EQUIVALENT";
}

export interface ManagementProfileResponse { configured: boolean; profile: ManagementProfile | null; revision: number; today?: string; timezone?: string }
export interface DraftRecord { entry_key: string; revision: number; recorded_at?: string; stale?: boolean | null; stale_reason?: string | null; payload: PlanningDraft }
export type Component = "Z1" | "Z2" | "Z3" | "Z4" | "Z5" | "STR";
export const COMPONENTS: Component[] = ["Z1", "Z2", "Z3", "Z4", "Z5", "STR"];
export interface SessionBlock { kind: string; label: string; zone: string; duration_min: number; target_hr_bpm: number | null; target_speed_kmh: number | null; repetition: number | null; instructions: string; primary_control?: string; speed_basis?: string }
export interface DoseEvidence {
  capacity_source: string; capacity_minutes: number; target_hr_bpm: number | null; target_speed_kmh: number | null;
  fraction: number; requested_work_minutes: number; prescribed_work_minutes: number;
  limits: Array<{ code: string; limit_minutes: number }>; fallback_reasons: string[]; model_version: string;
  explanation: string; technical_spill_reference: Record<Component, number>;
}
export interface DraftSession {
  method_id: string; title: string; sport: string; zone: string; purpose: string; blocks: SessionBlock[];
  main_work_minutes: number; total_minutes: number; canonical_effective_load: Record<Component, number>;
  direct_equivalent_minutes: Record<Component, number>; dose_evidence: DoseEvidence;
}
export interface DraftDay {
  date: string; status: string; period: string; taper: boolean; session: DraftSession | null;
  readiness_before: Record<Component, number | null>; readiness_after: Record<Component, number | null>;
  load_budget: { remaining_weekly_minutes: number; components: Record<Component, { e7_daily: number; e40_daily: number; index_7_40: number | null; target_weekly_effective: number; rolling_7d_effective: number; deficit_effective: number }> };
  explanation: string; rejected_alternatives: Array<{ method_id: string; reason: string; code: string }>;
}
export interface PlanningDraft {
  schema_version: "planning-draft-v1";
  status: "DRAFT" | "LIMITED_DRAFT" | "BLOCKED";
  start_date: string;
  end_date: string;
  generated_at?: string;
  engine_version: string;
  days: DraftDay[];
  source: Record<string, unknown>;
  warnings: Array<{ code: string; message: string }>;
  [key: string]: unknown;
}

const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const range = (value: unknown, low: number, high: number): value is number => finite(value) && value >= low && value <= high;
const integer = (value: unknown, low: number, high: number): value is number => range(value, low, high) && Number.isInteger(value);
const optionalRange = (value: unknown, low: number, high: number) => value === null || range(value, low, high);

export function parseManagementProfile(value: unknown): ManagementProfile {
  if (!isRecord(value) || value.schema_version !== "management-profile-v1"
    || !["Run", "NordicSki"].includes(String(value.sport))
    || !["Run", "NordicSki", "RollerSki"].includes(String(value.actual_sport)) || (value.sport === "Run" && value.actual_sport !== "Run")
    || typeof value.discipline !== "string" || !value.discipline.trim() || value.discipline !== value.discipline.trim() || value.discipline.length > 100
    || !(value.age_years === null || integer(value.age_years, 10, 100))
    || !optionalRange(value.training_experience_years, 0, 85)
    || !(value.race_duration_min === null || range(value.race_duration_min, .01, 1440))
    || !isCalendarDate(value.program_start) || !isCalendarDate(value.program_end) || value.program_end < value.program_start
    || (Date.parse(value.program_end) - Date.parse(value.program_start)) > 365 * 86_400_000
    || (finite(value.age_years) && finite(value.training_experience_years) && value.training_experience_years > value.age_years)
    || !Array.isArray(value.available_minutes) || value.available_minutes.length !== 7 || !value.available_minutes.every(v => range(v, 0, 360))
    || (value.recent_weekly_hours !== null && (!Array.isArray(value.recent_weekly_hours) || value.recent_weekly_hours.length !== 4 || !value.recent_weekly_hours.every(v => range(v, 0, 80))))
    || !(value.reentry_days === null || integer(value.reentry_days, 0, 21))
    || !integer(value.taper_days, 0, 21) || !integer(value.max_key_sessions_per_week, 0, 3)
    || !range(value.building_fraction, .5, .6) || !range(value.maintenance_fraction, .3, .4) || !range(value.reentry_fraction, .4, .5)
    || !range(value.recovery_session_cap_min, 5, 45) || typeof value.allow_expert_fallback !== "boolean") {
    throw new Error("Проверете датите, наличното време и параметрите на профила.");
  }
  const normalized: Record<string, unknown> = { adaptation_mode: "AUTO", auto_import_enabled: true, progression_percent: 5, component_targets_weekly: {},
    interval_profiles: [], strength_enabled: false, strength_circuits: 2, transition_days: 0, ...value };
  if (typeof normalized.auto_import_enabled !== "boolean" || !["AUTO", "REVIEW"].includes(String(normalized.adaptation_mode)) || !range(normalized.progression_percent, 0, 10)
    || typeof normalized.strength_enabled !== "boolean" || !integer(normalized.strength_circuits, 2, 3)
    || !integer(normalized.transition_days, 0, 28) || !isRecord(normalized.component_targets_weekly)
    || !Object.entries(normalized.component_targets_weekly).every(([k, v]) => COMPONENTS.includes(k as Component) && range(v, 0, 3000))
    || !Array.isArray(normalized.interval_profiles) || normalized.interval_profiles.length > 2) throw new Error("Невалидни правила за адаптация или компонентни цели.");
  const seen = new Set<string>();
  for (const p of normalized.interval_profiles) {
    if (!isRecord(p) || !["Z4", "Z5"].includes(String(p.zone)) || seen.has(String(p.zone)) || p.sport !== value.actual_sport
      || !range(p.continuous_capacity_min, .001, 60) || !isCalendarDate(p.assessed_on)
      || typeof p.effort !== "string" || p.effort.trim().length < 8 || p.effort.length > 250
      || !integer(p.work_seconds, 15, 360) || !integer(p.recovery_seconds, 15, 600)
      || !integer(p.min_repetitions, 2, 20) || !integer(p.max_repetitions, p.min_repetitions, 20)
      || !range(p.total_capacity_ratio, .001, 3) || !integer(p.reserve_repetitions, 1, 4)
      || !optionalRange(p.target_speed_kmh, .001, 80) || (p.speed_basis !== undefined && !["ACTUAL", "FLAT_EQUIVALENT"].includes(String(p.speed_basis)))
      || p.work_seconds >= p.continuous_capacity_min * 60
      || p.min_repetitions * p.work_seconds > p.continuous_capacity_min * 60 * p.total_capacity_ratio
      || !range(value.age_years, 18, 100) || !range(value.training_experience_years, 1, 85)) throw new Error("Проверете целия интервален профил, възрастта и стажа. Минималната структура трябва да се побира в дозата.");
    seen.add(String(p.zone));
  }
  return normalized as unknown as ManagementProfile;
}

export function parseManagementProfileResponse(value: unknown): ManagementProfileResponse {
  if (!isRecord(value) || typeof value.configured !== "boolean" || !integer(value.revision, 0, Number.MAX_SAFE_INTEGER)) throw new Error("Невалиден профил за управление.");
  if (value.today !== undefined && !isCalendarDate(value.today)) throw new Error("Невалидна местна дата на спортиста.");
  if (value.timezone !== undefined && typeof value.timezone !== "string") throw new Error("Невалидна часова зона на спортиста.");
  const metadata = { ...(typeof value.today === "string" ? { today: value.today } : {}), ...(typeof value.timezone === "string" ? { timezone: value.timezone } : {}) };
  if (!value.configured) {
    if (value.profile !== null) throw new Error("Неконфигурираният профил съдържа стойности.");
    return { configured: false, profile: null, revision: value.revision, ...metadata };
  }
  return { configured: true, profile: parseManagementProfile(value.profile), revision: value.revision, ...metadata };
}

export function parseDraftRecord(value: unknown): DraftRecord {
  if (!isRecord(value) || typeof value.entry_key !== "string" || !integer(value.revision, 1, Number.MAX_SAFE_INTEGER) || !isRecord(value.payload)) throw new Error("Невалидна версия на програмата.");
  if (value.stale !== undefined && value.stale !== null && typeof value.stale !== "boolean") throw new Error("Невалидна актуалност на програмата.");
  const draft = value.payload;
  if (draft.schema_version !== "planning-draft-v1" || !["DRAFT", "LIMITED_DRAFT", "BLOCKED"].includes(String(draft.status))
    || !isCalendarDate(draft.start_date) || !isCalendarDate(draft.end_date) || draft.end_date < draft.start_date
    || typeof draft.engine_version !== "string" || !isRecord(draft.source)
    || !Array.isArray(draft.warnings) || !draft.warnings.every(w => isRecord(w) && typeof w.message === "string" && typeof w.code === "string")
    || !Array.isArray(draft.days) || draft.days.length > 7) throw new Error("Неподдържана структура на тренировъчната програма.");
  for (const day of draft.days) {
    if (!isRecord(day) || !isCalendarDate(day.date) || day.date < draft.start_date || day.date > draft.end_date
      || typeof day.explanation !== "string" || typeof day.period !== "string" || typeof day.status !== "string" || typeof day.taper !== "boolean"
      || !isRecord(day.readiness_before) || !isRecord(day.readiness_after) || !isRecord(day.load_budget) || !isRecord(day.load_budget.components)
      || !Array.isArray(day.rejected_alternatives)) throw new Error("Невалидна дневна задача.");
    for (const component of COMPONENTS) {
      if (!optionalRange(day.readiness_before[component], 0, 100) || !optionalRange(day.readiness_after[component], 0, 100)) throw new Error("Невалидна оценка на готовността.");
    }
    if (day.session !== null) {
      const session = day.session;
      if (!isRecord(session) || typeof session.title !== "string" || typeof session.method_id !== "string"
        || !range(session.main_work_minutes, 0, 1440) || !range(session.total_minutes, 0, 1440)
        || session.main_work_minutes > session.total_minutes || !Array.isArray(session.blocks)
        || !isRecord(session.dose_evidence) || typeof session.dose_evidence.explanation !== "string"
        || !Array.isArray(session.dose_evidence.limits) || !Array.isArray(session.dose_evidence.fallback_reasons)) throw new Error("Невалидна дозировка на тренировката.");
      for (const block of session.blocks) if (!isRecord(block) || typeof block.label !== "string" || typeof block.instructions !== "string"
        || !range(block.duration_min, 0, 1440) || !optionalRange(block.target_hr_bpm, 0, 250) || !optionalRange(block.target_speed_kmh, 0, 150)) throw new Error("Невалиден тренировъчен блок.");
    }
  }
  return value as unknown as DraftRecord;
}

export function parseDrafts(value: unknown): DraftRecord[] {
  if (!isRecord(value) || !Array.isArray(value.drafts)) throw new Error("Историята на програмите не е достъпна.");
  return value.drafts.map(parseDraftRecord);
}

export function defaultManagementProfile(today: string): ManagementProfile {
  const end = new Date(`${today}T12:00:00Z`);
  end.setUTCDate(end.getUTCDate() + 84);
  return {
    schema_version: "management-profile-v1", sport: "Run", actual_sport: "Run", discipline: "",
    age_years: null, training_experience_years: null, race_duration_min: null,
    program_start: today, program_end: end.toISOString().slice(0, 10), available_minutes: [60, 60, 60, 60, 60, 90, 0],
    recent_weekly_hours: null, reentry_days: null, taper_days: 7, max_key_sessions_per_week: 2,
    building_fraction: .5, maintenance_fraction: .3, reentry_fraction: .4,
    recovery_session_cap_min: 30, allow_expert_fallback: true,
    adaptation_mode: "AUTO", auto_import_enabled: true, progression_percent: 5, component_targets_weekly: {}, interval_profiles: [],
    strength_enabled: false, strength_circuits: 2, transition_days: 0,
  };
}

export const PHASE_LABELS: Record<string, string> = {
  REENTRY: "Вработващ", RE_ENTRY: "Вработващ", GENERAL: "Общо подготвителен", GENERAL_PREPARATION: "Общо подготвителен",
  SPECIFIC: "Специално подготвителен", SPECIFIC_PREPARATION: "Специално подготвителен", SPECIAL_PREPARATION: "Специално подготвителен",
  PRECOMPETITION: "Предсъстезателен", PRE_COMPETITION: "Предсъстезателен",
  COMPETITION: "Състезателен", TRANSITION: "Преходен", TAPER: "Тейпър",
};
export const CAPACITY_LABELS: Record<string, string> = {
  SPEED_TIME: "Индивидуална скорост–време", SPEED_DURATION: "Индивидуална скорост–време",
  EXPERT_TREF: "Експертен Tref — резервна оценка", EXPERT_FALLBACK: "Експертен Tref — резервна оценка", EXPERT_CONTINUOUS_TREF: "Експертен Tref — резервна оценка",
  COACH_EFFORT_CAPACITY: "Индивидуална устойчивост при описаното усилие",
  STRENGTH_METHOD_PROFILE: "Отделен силов профил с упражнения и резерв",
};

export interface PlanChange { date: string; before: string | null; after: string | null; before_minutes: number; after_minutes: number; reason: string }
export interface PlanOutcome { date: string; status: string; planned_title: string | null; planned_minutes: number; actual_minutes: number | null }
export interface ActivePlanRecord {
  revision: number; stale: boolean; actionable: boolean; stale_reason?: string | null;
  payload: { schema_version: "active-plan-v2"; status: "ACTIVE" | "PAUSED" | "REVIEW_REQUIRED" | "COMPLETED";
    mode: "AUTO" | "REVIEW"; reason: string; plan: PlanningDraft; proposal: PlanningDraft | null;
    changes: PlanChange[]; outcomes: PlanOutcome[]; decisions: Record<string, { action: string; note: string }> };
}
export interface ActivePlanResponse { active: ActivePlanRecord | null; history: Array<{ revision: number; operation: string; recorded_at: string; reason: string; changes: PlanChange[] }> }
export function parseActivePlanResponse(value: unknown): ActivePlanResponse {
  if (!isRecord(value) || !Array.isArray(value.history)) throw new Error("Невалидна история на активния план.");
  if (value.active === null) return { active: null, history: [] };
  const row = value.active;
  if (!isRecord(row) || !integer(row.revision, 1, Number.MAX_SAFE_INTEGER) || typeof row.stale !== "boolean" || typeof row.actionable !== "boolean" || !isRecord(row.payload)) throw new Error("Невалидна активна програма.");
  const p = row.payload;
  if (p.schema_version !== "active-plan-v2" || !["ACTIVE", "PAUSED", "REVIEW_REQUIRED", "COMPLETED"].includes(String(p.status))
    || !["AUTO", "REVIEW"].includes(String(p.mode)) || typeof p.reason !== "string" || !Array.isArray(p.changes)
    || !Array.isArray(p.outcomes) || !isRecord(p.decisions)) throw new Error("Невалидно състояние на програмата.");
  parseDraftRecord({ entry_key: "active", revision: row.revision, payload: p.plan });
  if (p.proposal !== null) parseDraftRecord({ entry_key: "proposal", revision: row.revision, payload: p.proposal });
  return value as unknown as ActivePlanResponse;
}
