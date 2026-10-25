export const ZONES = ["Z1", "Z2", "Z3", "Z4", "Z5"] as const;
export type Zone = (typeof ZONES)[number];

export const TREF_BOUNDS_MINUTES: Record<Zone, readonly [number, number]> = {
  Z1: [180, 300],
  Z2: [90, 180],
  Z3: [40, 70],
  Z4: [10, 20],
  Z5: [10, 20],
};

export interface ZoneTrainingStatus {
  zone: Zone;
  raw_time_min: number;
  equivalent_time_min: number;
  tref_min: number;
  status_7_40: number;
  recovery_readiness_percent: number;
  recovery_days_to_full: number;
}

export interface LoadSelectionMetadata {
  schema_version: "load-source-metadata-v1";
  scope: "RECORDED_AEROBIC_ACTIVITIES_IN_PERIOD";
  leading_indicator: "HEART_RATE" | "SPEED" | "MIXED";
  ledger_versions: string[]; hr_coordinate_policies: string[];
  activity_count: number; total_aerobic_activities: number;
  recorded_seconds: number; active_seconds: number; rest_seconds: number;
  unknown_seconds: number; fallback_seconds: number;
  source_seconds: { HEART_RATE: number; SPEED: number };
  hr_source_seconds: Record<string, number> | null;
  speed_status_seconds: Record<string, number> | null;
  speed_unavailable_reasons_seconds: Record<string, number> | null;
  unavailable_speed_hr_seconds: number | null; estimated_descent_seconds: number | null;
}

export interface TrainingStatus {
  schema_version: "training-status-v1";
  as_of: string;
  athlete_id: string;
  model: {
    algorithm_version: string;
    effective_hr_version: string;
    effective_hr_source: string;
    parameter_version: number;
    load_selection?: LoadSelectionMetadata | null;
  };
  data_quality: {
    history_reliability: number;
    latest_activity_quality_score: number | null;
    warnings: string[];
  };
  zones: ZoneTrainingStatus[];
}

const rootKeys = ["schema_version", "as_of", "athlete_id", "model", "data_quality", "zones"];
const modelKeys = ["algorithm_version", "effective_hr_version", "effective_hr_source", "parameter_version"];
const qualityKeys = ["history_reliability", "latest_activity_quality_score", "warnings"];
const zoneKeys = ["zone", "raw_time_min", "equivalent_time_min", "tref_min", "status_7_40", "recovery_readiness_percent", "recovery_days_to_full"];

export const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
export const exactKeys = (value: Record<string, unknown>, keys: string[]) =>
  Object.keys(value).length === keys.length && keys.every((key) => key in value);
export const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const nonnegative = (value: unknown): value is number => finite(value) && value >= 0;
const secondsMap = (value: unknown) => isRecord(value) && Object.values(value).every(nonnegative);

export function isLoadSelectionMetadata(value: unknown): value is LoadSelectionMetadata {
  if (!isRecord(value) || value.schema_version !== "load-source-metadata-v1"
    || value.scope !== "RECORDED_AEROBIC_ACTIVITIES_IN_PERIOD"
    || !["HEART_RATE", "SPEED", "MIXED"].includes(String(value.leading_indicator))
    || ![value.ledger_versions, value.hr_coordinate_policies].every(v => Array.isArray(v) && v.every(x => typeof x === "string"))
    || !Number.isSafeInteger(value.activity_count) || !Number.isSafeInteger(value.total_aerobic_activities)
    || Number(value.activity_count) < 1 || Number(value.total_aerobic_activities) < Number(value.activity_count)
    || ![value.recorded_seconds, value.active_seconds, value.rest_seconds, value.unknown_seconds, value.fallback_seconds].every(nonnegative)
    || !secondsMap(value.source_seconds) || !isRecord(value.source_seconds)
    || !exactKeys(value.source_seconds, ["HEART_RATE", "SPEED"])
    || ![value.hr_source_seconds, value.speed_status_seconds, value.speed_unavailable_reasons_seconds].every(v => v === null || secondsMap(v))
    || ![value.unavailable_speed_hr_seconds, value.estimated_descent_seconds].every(v => v === null || nonnegative(v))) return false;
  const seconds = value.source_seconds;
  const recorded = Number(value.recorded_seconds);
  const tolerance = Math.max(.001, recorded * 1e-9);
  const sum = (items: Record<string, unknown>) => Object.values(items).reduce<number>((total, item) => total + Number(item), 0);
  if (isRecord(value.hr_source_seconds) && Math.abs(sum(value.hr_source_seconds) - Number(seconds.HEART_RATE)) > tolerance) return false;
  if (isRecord(value.speed_status_seconds)
    && (!exactKeys(value.speed_status_seconds, ["ACTIVE", "REST", "UNAVAILABLE"])
      || Math.abs(sum(value.speed_status_seconds) - recorded) > tolerance)) return false;
  if (isRecord(value.speed_unavailable_reasons_seconds) && isRecord(value.speed_status_seconds)
    && Math.abs(sum(value.speed_unavailable_reasons_seconds) - Number(value.speed_status_seconds.UNAVAILABLE)) > tolerance) return false;
  if (nonnegative(value.unavailable_speed_hr_seconds) && value.unavailable_speed_hr_seconds > Number(seconds.HEART_RATE) + tolerance) return false;
  return Math.abs(recorded - Number(value.active_seconds) - Number(value.rest_seconds) - Number(value.unknown_seconds)) <= tolerance
    && Math.abs(recorded - Number(seconds.HEART_RATE) - Number(seconds.SPEED) - Number(value.unknown_seconds)) <= tolerance
    && Number(value.fallback_seconds) <= recorded + tolerance;
}
export const isCalendarDate = (value: unknown): value is string => {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const [year, month, day] = value.split("-").map(Number);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  return parsed.getUTCFullYear() === year && parsed.getUTCMonth() === month - 1 && parsed.getUTCDate() === day;
};

export function parseTrainingStatus(value: unknown): TrainingStatus {
  if (!isRecord(value) || !exactKeys(value, rootKeys)) throw new Error("Невалидна структура на отговора.");
  if (value.schema_version !== "training-status-v1") throw new Error("Неподдържана версия на API договора.");
  if (!isCalendarDate(value.as_of)) throw new Error("Невалидна дата на анализа.");
  if (typeof value.athlete_id !== "string" || value.athlete_id.length === 0) throw new Error("Липсва идентификатор на спортист.");
  if (!isRecord(value.model) || !exactKeys(value.model, "load_selection" in value.model ? [...modelKeys, "load_selection"] : modelKeys) ||
      typeof value.model.algorithm_version !== "string" || typeof value.model.effective_hr_version !== "string" ||
      typeof value.model.effective_hr_source !== "string" || !Number.isInteger(value.model.parameter_version)
      || value.model.load_selection != null && !isLoadSelectionMetadata(value.model.load_selection)) {
    throw new Error("Невалидни метаданни на модела.");
  }
  if (!isRecord(value.data_quality) || !exactKeys(value.data_quality, qualityKeys) ||
      !finite(value.data_quality.history_reliability) ||
      !(value.data_quality.latest_activity_quality_score === null || finite(value.data_quality.latest_activity_quality_score)) ||
      !Array.isArray(value.data_quality.warnings) || !value.data_quality.warnings.every((warning) => typeof warning === "string")) {
    throw new Error("Невалидно обобщение за качеството на данните.");
  }
  if (!Array.isArray(value.zones) || value.zones.length !== ZONES.length) {
    throw new Error("Зоналните данни трябва да съдържат точно Z1–Z5.");
  }
  const zones = value.zones.map((item, index) => {
    if (!isRecord(item) || !exactKeys(item, zoneKeys) || item.zone !== ZONES[index] ||
        !zoneKeys.slice(1).every((key) => finite(item[key]))) {
      throw new Error("Невалидни или неподредени зонални данни.");
    }
    const [minimumTref, maximumTref] = TREF_BOUNDS_MINUTES[ZONES[index]];
    if (Number(item.tref_min) < minimumTref || Number(item.tref_min) > maximumTref) {
      throw new Error("Tref е извън одобрените зонални граници.");
    }
    return item as unknown as ZoneTrainingStatus;
  });
  return { ...value, model: value.model, data_quality: value.data_quality, zones } as TrainingStatus;
}
