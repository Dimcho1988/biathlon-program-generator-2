import { exactKeys, finite, isCalendarDate, isRecord, ZONES, type Zone } from "./training-status";

export interface RecoveryHistory {
  schema_version: "recovery-history-v1";
  athlete_id: string;
  period_start: string;
  period_end: string;
  basis: "load-only";
  wellness_freshness: "fresh" | "stale" | "unknown";
  wellness_coverage_percent: number;
  model: {
    algorithm_version: string;
    parameter_version: string;
    parameter_fingerprint: string;
    practical_full_recovery_percent: number;
  };
  settings: Array<{
    zone: Zone;
    tref_min: number;
    sensitivity: number;
    tau_days: number;
    fatigue_cap: number;
  }>;
  current: Array<{
    zone: Zone;
    readiness_percent: number;
    residual_fatigue: number;
    days_to_practical_recovery: number;
  }>;
  daily: Array<{
    date: string;
    zone: Zone;
    readiness_before_percent: number;
    readiness_after_percent: number;
    residual_fatigue_after: number;
    impulse: number;
    effective_load: number;
    tref_min: number;
  }>;
}

const rootKeys = ["schema_version", "athlete_id", "period_start", "period_end", "basis", "wellness_freshness", "wellness_coverage_percent", "model", "settings", "current", "daily"];
const modelKeys = ["algorithm_version", "parameter_version", "parameter_fingerprint", "practical_full_recovery_percent"];
const settingKeys = ["zone", "tref_min", "sensitivity", "tau_days", "fatigue_cap"];
const currentKeys = ["zone", "readiness_percent", "residual_fatigue", "days_to_practical_recovery"];
const dailyKeys = ["date", "zone", "readiness_before_percent", "readiness_after_percent", "residual_fatigue_after", "impulse", "effective_load", "tref_min"];
const percentage = (value: unknown) => finite(value) && value >= 0 && value <= 100;
const zoneAt = (value: unknown, index: number) => value === ZONES[index];

function zoneArray<T>(value: unknown, parser: (item: unknown, index: number) => T): T[] {
  if (!Array.isArray(value) || value.length !== ZONES.length) throw new Error("Recovery данните трябва да съдържат точно Z1–Z5.");
  return value.map(parser);
}

export function parseRecoveryHistory(value: unknown): RecoveryHistory {
  if (!isRecord(value) || !exactKeys(value, rootKeys)) throw new Error("Невалидна структура на recovery историята.");
  if (value.schema_version !== "recovery-history-v1" || value.basis !== "load-only") throw new Error("Неподдържана recovery версия или основа.");
  if (typeof value.athlete_id !== "string" || !value.athlete_id ||
      !isCalendarDate(value.period_start) || !isCalendarDate(value.period_end) || value.period_start > value.period_end ||
      !(value.wellness_freshness === "fresh" || value.wellness_freshness === "stale" || value.wellness_freshness === "unknown") ||
      !percentage(value.wellness_coverage_percent)) throw new Error("Невалиден recovery контекст.");
  if (!isRecord(value.model)) throw new Error("Невалидни recovery метаданни.");
  const model = value.model;
  if (!exactKeys(model, modelKeys) ||
      !modelKeys.slice(0, 3).every((key) => typeof model[key] === "string" && model[key] !== "") ||
      !percentage(model.practical_full_recovery_percent)) throw new Error("Невалидни recovery метаданни.");

  const settings = zoneArray(value.settings, (item, index) => {
    if (!isRecord(item) || !exactKeys(item, settingKeys) || !zoneAt(item.zone, index) ||
        !settingKeys.slice(1).every((key) => finite(item[key]) && item[key] >= 0)) throw new Error("Невалидни recovery настройки.");
    return item as unknown as RecoveryHistory["settings"][number];
  });
  const current = zoneArray(value.current, (item, index) => {
    if (!isRecord(item) || !exactKeys(item, currentKeys) || !zoneAt(item.zone, index) ||
        !percentage(item.readiness_percent) || !currentKeys.slice(2).every((key) => finite(item[key]) && item[key] >= 0)) {
      throw new Error("Невалиден текущ recovery статус.");
    }
    return item as unknown as RecoveryHistory["current"][number];
  });
  if (!Array.isArray(value.daily) || value.daily.length % ZONES.length !== 0) throw new Error("Невалидна дневна recovery история.");
  const daily = value.daily.map((item, index) => {
    if (!isRecord(item) || !exactKeys(item, dailyKeys) || !isCalendarDate(item.date) || !zoneAt(item.zone, index % ZONES.length) ||
        !percentage(item.readiness_before_percent) || !percentage(item.readiness_after_percent) ||
        !dailyKeys.slice(4).every((key) => finite(item[key]) && item[key] >= 0)) throw new Error("Невалиден дневен recovery ред.");
    return item as unknown as RecoveryHistory["daily"][number];
  });
  return { ...value, settings, current, daily } as unknown as RecoveryHistory;
}
