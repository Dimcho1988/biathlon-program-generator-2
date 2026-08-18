import { exactKeys, finite, isCalendarDate, isRecord } from "./training-status";

export interface WeeklyVolume {
  week_start: string;
  week_end: string;
  observed_days: number;
  activities_count: number;
  limited_activities: number;
  missing_duration_activities: number;
  activity_duration_min: number;
  zoned_hr_time_min: number;
}

export interface VolumeHistory {
  schema_version: "volume-history-v1";
  athlete_id: string;
  period_start: string;
  period_end: string;
  model: {
    aggregation_version: "volume-history-calendar-week-v1";
    source_schema_version: "load-history-v1";
    calendar_week_start: "monday";
    activity_duration_handling: "known-values-only";
  };
  quality: {
    modeled_activities: number;
    limited_activities: number;
    missing_duration_activities: number;
  };
  weekly: WeeklyVolume[];
}

const rootKeys = ["schema_version", "athlete_id", "period_start", "period_end", "model", "quality", "weekly"];
const modelKeys = ["aggregation_version", "source_schema_version", "calendar_week_start", "activity_duration_handling"];
const qualityKeys = ["modeled_activities", "limited_activities", "missing_duration_activities"];
const weeklyKeys = ["week_start", "week_end", "observed_days", "activities_count", "limited_activities", "missing_duration_activities", "activity_duration_min", "zoned_hr_time_min"];
const nonNegativeInteger = (value: unknown) => Number.isInteger(value) && Number(value) >= 0;
const nonNegativeFinite = (value: unknown) => finite(value) && value >= 0;
const utcDate = (value: string) => new Date(`${value}T00:00:00Z`);

export function parseVolumeHistory(value: unknown): VolumeHistory {
  if (!isRecord(value) || !exactKeys(value, rootKeys)) throw new Error("Невалидна структура на обемната история.");
  if (value.schema_version !== "volume-history-v1") throw new Error("Неподдържана версия на обемната история.");
  if (typeof value.athlete_id !== "string" || value.athlete_id.length === 0) throw new Error("Липсва идентификатор на спортист.");
  if (!isCalendarDate(value.period_start) || !isCalendarDate(value.period_end) || value.period_start > value.period_end) throw new Error("Невалиден период на обемната история.");
  if (!isRecord(value.model) || !exactKeys(value.model, modelKeys) ||
      value.model.aggregation_version !== "volume-history-calendar-week-v1" ||
      value.model.source_schema_version !== "load-history-v1" ||
      value.model.calendar_week_start !== "monday" ||
      value.model.activity_duration_handling !== "known-values-only") {
    throw new Error("Невалидни метаданни на обемната история.");
  }
  const quality = value.quality;
  if (!isRecord(quality) || !exactKeys(quality, qualityKeys) ||
      !qualityKeys.every((key) => nonNegativeInteger(quality[key]))) {
    throw new Error("Невалидно качество на обемната история.");
  }
  if (!Array.isArray(value.weekly) || value.weekly.length === 0) throw new Error("Липсва седмична обемна история.");
  const weekly = value.weekly.map((item, index) => {
    if (!isRecord(item) || !exactKeys(item, weeklyKeys) || !isCalendarDate(item.week_start) || !isCalendarDate(item.week_end) ||
        utcDate(item.week_start).getUTCDay() !== 1 || utcDate(item.week_end).getTime() - utcDate(item.week_start).getTime() !== 6 * 86_400_000 ||
        !Number.isInteger(item.observed_days) || Number(item.observed_days) < 1 || Number(item.observed_days) > 7 ||
        !weeklyKeys.slice(3, 6).every((key) => nonNegativeInteger(item[key])) ||
        !nonNegativeFinite(item.activity_duration_min) || !nonNegativeFinite(item.zoned_hr_time_min)) {
      throw new Error("Невалиден седмичен обемен ред.");
    }
    if (index > 0 && utcDate(item.week_start).getTime() - utcDate((value.weekly as Array<Record<string, unknown>>)[index - 1].week_start as string).getTime() !== 7 * 86_400_000) {
      throw new Error("Седмичната обемна история не е последователна.");
    }
    return item as unknown as WeeklyVolume;
  });
  const sums = weekly.reduce((total, row) => ({
    modeled: total.modeled + row.activities_count,
    limited: total.limited + row.limited_activities,
    missing: total.missing + row.missing_duration_activities,
  }), { modeled: 0, limited: 0, missing: 0 });
  if (sums.modeled !== quality.modeled_activities || sums.limited !== quality.limited_activities || sums.missing !== quality.missing_duration_activities) {
    throw new Error("Седмичните редове не съответстват на обобщението за качество.");
  }
  return { ...value, model: value.model, quality, weekly } as unknown as VolumeHistory;
}
