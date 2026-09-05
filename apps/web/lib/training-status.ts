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

export interface TrainingStatus {
  schema_version: "training-status-v1";
  as_of: string;
  athlete_id: string;
  model: {
    algorithm_version: string;
    effective_hr_version: string;
    effective_hr_source: string;
    parameter_version: number;
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
  if (!isRecord(value.model) || !exactKeys(value.model, modelKeys) ||
      typeof value.model.algorithm_version !== "string" || typeof value.model.effective_hr_version !== "string" ||
      typeof value.model.effective_hr_source !== "string" || !Number.isInteger(value.model.parameter_version)) {
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
