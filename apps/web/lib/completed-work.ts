import { exactKeys, finite, isCalendarDate, isRecord, ZONES, type Zone } from "./training-status";

export interface CompletedWorkZone {
  zone: Zone;
  raw_time_min: number;
  equivalent_time_min: number;
  effective_load: number;
}

export interface CompletedWorkSport {
  sport: string;
  activities_count: number;
  activity_duration_min: number;
  zoned_hr_time_min: number;
}

export interface CompletedWork {
  schema_version: "completed-work-v1";
  athlete_id: string;
  period_start: string;
  period_end: string;
  model: {
    aggregation_version: "completed-work-snapshot-aggregation-v1";
    source_schema_version: "load-history-v1";
    sport_grouping: "provider-label-exact";
  };
  quality: {
    modeled_activities: number;
    limited_activities: number;
    missing_duration_activities: number;
  };
  totals: {
    activity_duration_min: number;
    zoned_hr_time_min: number;
  };
  zones: CompletedWorkZone[];
  sports: CompletedWorkSport[];
}

const rootKeys = ["schema_version", "athlete_id", "period_start", "period_end", "model", "quality", "totals", "zones", "sports"];
const modelKeys = ["aggregation_version", "source_schema_version", "sport_grouping"];
const qualityKeys = ["modeled_activities", "limited_activities", "missing_duration_activities"];
const totalKeys = ["activity_duration_min", "zoned_hr_time_min"];
const zoneKeys = ["zone", "raw_time_min", "equivalent_time_min", "effective_load"];
const sportKeys = ["sport", "activities_count", "activity_duration_min", "zoned_hr_time_min"];
const nonNegative = (value: unknown): value is number => finite(value) && value >= 0;
const nonNegativeInteger = (value: unknown) => Number.isInteger(value) && Number(value) >= 0;

export function parseCompletedWork(value: unknown): CompletedWork {
  if (!isRecord(value) || !exactKeys(value, rootKeys)) throw new Error("Невалидна структура на отчета.");
  if (value.schema_version !== "completed-work-v1") throw new Error("Неподдържана версия на отчета.");
  if (typeof value.athlete_id !== "string" || !value.athlete_id) throw new Error("Липсва идентификатор на спортист.");
  if (!isCalendarDate(value.period_start) || !isCalendarDate(value.period_end) || value.period_start > value.period_end) throw new Error("Невалиден период на отчета.");
  if (!isRecord(value.model) || !exactKeys(value.model, modelKeys) ||
      value.model.aggregation_version !== "completed-work-snapshot-aggregation-v1" ||
      value.model.source_schema_version !== "load-history-v1" ||
      value.model.sport_grouping !== "provider-label-exact") throw new Error("Невалидни метаданни на отчета.");
  if (!isRecord(value.quality)) throw new Error("Невалидно качество на отчета.");
  const quality = value.quality;
  if (!exactKeys(quality, qualityKeys) ||
      !qualityKeys.every((key) => nonNegativeInteger(quality[key]))) throw new Error("Невалидно качество на отчета.");
  if (!isRecord(value.totals)) throw new Error("Невалидни общи стойности на отчета.");
  const totals = value.totals;
  if (!exactKeys(totals, totalKeys) ||
      !totalKeys.every((key) => nonNegative(totals[key]))) throw new Error("Невалидни общи стойности на отчета.");
  if (!Array.isArray(value.zones) || value.zones.length !== ZONES.length) throw new Error("Отчетът трябва да съдържа точно Z1–Z5.");
  const zones = value.zones.map((item, index) => {
    if (!isRecord(item) || !exactKeys(item, zoneKeys) || item.zone !== ZONES[index] ||
        !zoneKeys.slice(1).every((key) => nonNegative(item[key]))) throw new Error("Невалидни или неподредени зонални стойности в отчета.");
    return item as unknown as CompletedWorkZone;
  });
  if (!Array.isArray(value.sports)) throw new Error("Невалидно групиране по вид активност.");
  const seen = new Set<string>();
  const sports = value.sports.map((item) => {
    if (!isRecord(item) || !exactKeys(item, sportKeys) || typeof item.sport !== "string" || !item.sport || seen.has(item.sport) ||
        !nonNegativeInteger(item.activities_count) || !nonNegative(item.activity_duration_min) || !nonNegative(item.zoned_hr_time_min)) {
      throw new Error("Невалиден ред за вид активност.");
    }
    seen.add(item.sport);
    return item as unknown as CompletedWorkSport;
  });
  return { ...value, model: value.model, quality, totals, zones, sports } as CompletedWork;
}
