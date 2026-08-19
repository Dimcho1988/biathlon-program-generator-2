import { exactKeys, finite, isCalendarDate, isRecord, ZONES, type Zone } from "./training-status";

export interface ZoneLoadSummary {
  zone: Zone;
  e7_daily: number;
  e40_daily: number;
  status_7_40: number;
  tref_min: number;
  history_reliability: number;
}

export interface DailyZoneLoad {
  date: string;
  zone: Zone;
  effective_load: number;
  e7_daily: number;
  e40_daily: number;
  status_7_40: number;
}

export interface ActivityZoneLoad {
  zone: Zone;
  raw_time_min: number;
  equivalent_time_min: number;
  effective_load: number;
  mean_effective_hr_bpm: number | null;
  average_minute_value_percent: number | null;
}

export interface LoadHistoryActivity {
  activity_ref: string;
  date: string;
  sport: string;
  duration_min: number | null;
  strength_time_min: number;
  quality_status: "valid" | "limited";
  hr_coverage_percent: number;
  zones: ActivityZoneLoad[];
}

export interface StrengthLoadHistory {
  model: {
    classification_version: string;
    source: "intervals-activity-type-duration";
    duration_basis: "recording-time-first";
    equivalent_time_coefficient: number;
    aerobic_hr_counted: false;
  };
  summary: {
    recorded_activities: number;
    real_time_7d_min: number;
    real_time_40d_min: number;
    e7_daily: number;
    e40_daily: number;
    status_7_40: number;
    tref_min: number;
    history_reliability: number;
  };
  daily: Array<{
    date: string;
    real_time_min: number;
    equivalent_time_min: number;
    effective_load: number;
    e7_daily: number;
    e40_daily: number;
    status_7_40: number;
  }>;
}

export interface LoadHistory {
  schema_version: "load-history-v1";
  athlete_id: string;
  period_start: string;
  period_end: string;
  quality: {
    processed_activities: number;
    limited_activities: number;
    excluded_activities: number;
    no_activity_days: number;
    warnings: string[];
  };
  zones: ZoneLoadSummary[];
  daily: DailyZoneLoad[];
  activities: LoadHistoryActivity[];
  strength?: StrengthLoadHistory | null;
}

const legacyRootKeys = ["schema_version", "athlete_id", "period_start", "period_end", "quality", "zones", "daily", "activities"];
const rootKeys = [...legacyRootKeys, "strength"];
const qualityKeys = ["processed_activities", "limited_activities", "excluded_activities", "no_activity_days", "warnings"];
const summaryKeys = ["zone", "e7_daily", "e40_daily", "status_7_40", "tref_min", "history_reliability"];
const dailyKeys = ["date", "zone", "effective_load", "e7_daily", "e40_daily", "status_7_40"];
const legacyActivityKeys = ["activity_ref", "date", "sport", "duration_min", "quality_status", "hr_coverage_percent", "zones"];
const activityKeys = ["activity_ref", "date", "sport", "duration_min", "strength_time_min", "quality_status", "hr_coverage_percent", "zones"];
const activityZoneKeys = ["zone", "raw_time_min", "equivalent_time_min", "effective_load", "mean_effective_hr_bpm", "average_minute_value_percent"];
const strengthKeys = ["model", "summary", "daily"];
const strengthModelKeys = ["classification_version", "source", "duration_basis", "equivalent_time_coefficient", "aerobic_hr_counted"];
const strengthSummaryKeys = ["recorded_activities", "real_time_7d_min", "real_time_40d_min", "e7_daily", "e40_daily", "status_7_40", "tref_min", "history_reliability"];
const strengthDailyKeys = ["date", "real_time_min", "equivalent_time_min", "effective_load", "e7_daily", "e40_daily", "status_7_40"];

const zoneAt = (value: unknown, index: number) => value === ZONES[index];
const optionalFinite = (value: unknown) => value === null || finite(value);
const nonNegativeInteger = (value: unknown) => Number.isInteger(value) && Number(value) >= 0;
const percentageTolerance = 1e-6;
const boundedPercentage = (value: unknown) => finite(value) && value >= -percentageTolerance && value <= 100 + percentageTolerance;
const normalizePercentage = (value: number) => Math.min(100, Math.max(0, value));

function parseZoneArray<T>(value: unknown, parser: (item: unknown, index: number) => T): T[] {
  if (!Array.isArray(value) || value.length !== ZONES.length) throw new Error("Зоналните данни трябва да съдържат точно Z1–Z5.");
  return value.map(parser);
}

export function parseLoadHistory(value: unknown): LoadHistory {
  if (!isRecord(value) || (!exactKeys(value, rootKeys) && !exactKeys(value, legacyRootKeys))) throw new Error("Невалидна структура на историята.");
  if (value.schema_version !== "load-history-v1") throw new Error("Неподдържана версия на историята.");
  if (typeof value.athlete_id !== "string" || value.athlete_id.length === 0) throw new Error("Липсва идентификатор на спортист.");
  if (!isCalendarDate(value.period_start) || !isCalendarDate(value.period_end) || value.period_start > value.period_end) throw new Error("Невалиден период на историята.");
  if (!isRecord(value.quality)) throw new Error("Невалидно качество на историята.");
  const quality = value.quality;
  if (!exactKeys(quality, qualityKeys) ||
      !qualityKeys.slice(0, 4).every((key) => nonNegativeInteger(quality[key])) ||
      !Array.isArray(quality.warnings) || !quality.warnings.every((warning) => typeof warning === "string")) {
    throw new Error("Невалидно качество на историята.");
  }

  const zones = parseZoneArray(value.zones, (item, index) => {
    if (!isRecord(item) || !exactKeys(item, summaryKeys) || !zoneAt(item.zone, index) ||
        !summaryKeys.slice(1).every((key) => finite(item[key]))) throw new Error("Невалидно зонално обобщение.");
    return item as unknown as ZoneLoadSummary;
  });

  if (!Array.isArray(value.daily) || value.daily.length % ZONES.length !== 0) throw new Error("Невалидна дневна история.");
  const daily = value.daily.map((item, index) => {
    if (!isRecord(item) || !exactKeys(item, dailyKeys) || !isCalendarDate(item.date) ||
        !zoneAt(item.zone, index % ZONES.length) || !dailyKeys.slice(2).every((key) => finite(item[key]))) {
      throw new Error("Невалиден дневен ред.");
    }
    if (index > 0 && index % ZONES.length !== 0 && item.date !== (value.daily as Array<Record<string, unknown>>)[index - 1].date) throw new Error("Непълна дневна зонална група.");
    return item as unknown as DailyZoneLoad;
  });

  if (!Array.isArray(value.activities)) throw new Error("Невалиден списък с активности.");
  const references = new Set<string>();
  const activities = value.activities.map((item) => {
    if (!isRecord(item) || (!exactKeys(item, activityKeys) && !exactKeys(item, legacyActivityKeys)) || typeof item.activity_ref !== "string" || !item.activity_ref ||
        references.has(item.activity_ref) || !isCalendarDate(item.date) || typeof item.sport !== "string" || !item.sport ||
        !optionalFinite(item.duration_min) || (finite(item.duration_min) && item.duration_min < 0) ||
        !(item.strength_time_min === undefined || (finite(item.strength_time_min) && item.strength_time_min >= 0)) ||
        !(["valid", "limited"] as unknown[]).includes(item.quality_status) || !boundedPercentage(item.hr_coverage_percent)) {
      throw new Error("Невалидна активност в историята.");
    }
    references.add(item.activity_ref);
    const activityZones = parseZoneArray(item.zones, (zone, index) => {
      if (!isRecord(zone) || !exactKeys(zone, activityZoneKeys) || !zoneAt(zone.zone, index) ||
          !activityZoneKeys.slice(1, 4).every((key) => finite(zone[key])) ||
          !optionalFinite(zone.mean_effective_hr_bpm) || !optionalFinite(zone.average_minute_value_percent)) {
        throw new Error("Невалиден зонален детайл на активност.");
      }
      return zone as unknown as ActivityZoneLoad;
    });
    return {
      ...item,
      strength_time_min: item.strength_time_min ?? 0,
      hr_coverage_percent: normalizePercentage(item.hr_coverage_percent as number),
      zones: activityZones,
    } as unknown as LoadHistoryActivity;
  });

  let strength: StrengthLoadHistory | null | undefined;
  if ("strength" in value) {
    if (value.strength === null) strength = null;
    else {
      const raw = value.strength;
      if (!isRecord(raw) || !exactKeys(raw, strengthKeys) || !isRecord(raw.model) || !isRecord(raw.summary)) throw new Error("Невалидна история на силовото натоварване.");
      const strengthModel = raw.model;
      const strengthSummary = raw.summary;
      if (!exactKeys(strengthModel, strengthModelKeys) || !exactKeys(strengthSummary, strengthSummaryKeys) ||
          typeof strengthModel.classification_version !== "string" || !strengthModel.classification_version ||
          strengthModel.source !== "intervals-activity-type-duration" || strengthModel.duration_basis !== "recording-time-first" ||
          !finite(strengthModel.equivalent_time_coefficient) || strengthModel.equivalent_time_coefficient <= 0 || strengthModel.aerobic_hr_counted !== false ||
          !nonNegativeInteger(strengthSummary.recorded_activities) ||
          !strengthSummaryKeys.slice(1).every((key) => finite(strengthSummary[key]) && strengthSummary[key] >= 0) ||
          !Array.isArray(raw.daily)) throw new Error("Невалидна история на силовото натоварване.");
      const expectedDays = value.daily.length / ZONES.length;
      if (raw.daily.length !== expectedDays) throw new Error("Непълна дневна история на силовото натоварване.");
      const strengthDaily = raw.daily.map((item, index) => {
        if (!isRecord(item) || !exactKeys(item, strengthDailyKeys) || !isCalendarDate(item.date) ||
            item.date !== (value.daily as Array<Record<string, unknown>>)[index * ZONES.length]?.date ||
            !strengthDailyKeys.slice(1).every((key) => finite(item[key]) && item[key] >= 0)) {
          throw new Error("Невалиден дневен силов ред.");
        }
        return item as unknown as StrengthLoadHistory["daily"][number];
      });
      strength = { ...raw, model: strengthModel, summary: strengthSummary, daily: strengthDaily } as unknown as StrengthLoadHistory;
    }
  }

  return { ...value, quality, zones, daily, activities, strength } as unknown as LoadHistory;
}
