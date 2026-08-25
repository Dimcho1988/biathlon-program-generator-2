export type ActivityQuality = "valid" | "limited" | "excluded" | "provider_missing";
export type ActivityZone = "Z1" | "Z2" | "Z3" | "Z4" | "Z5";

export interface ActivityZoneSummary {
  zone: ActivityZone;
  raw_time_s: number;
  equivalent_time_s: number;
  effective_load: number;
}

export interface ActivityHrmodZoneSummary {
  zone: ActivityZone;
  final_time_s: number;
}

export interface DailyWellnessMetric {
  value: number | boolean;
  unit: string;
}

export interface DailyWellnessSummary {
  date: string;
  metrics: Record<string, DailyWellnessMetric>;
}

export interface ActivityCalendarItem {
  activity_ref: string;
  start_at_utc: string;
  start_local: string;
  local_date: string;
  local_time: string;
  timezone: string | null;
  utc_offset_minutes: number | null;
  sport: string;
  activity_type: string | null;
  activity_sub_type: string | null;
  name: string | null;
  duration_min: number | null;
  distance_m: number | null;
  elevation_gain_m: number | null;
  average_hr_bpm: number | null;
  max_hr_bpm: number | null;
  average_speed_mps: number | null;
  max_speed_mps: number | null;
  canonical_training_load: number | null;
  quality_status: ActivityQuality;
  quality_reason: string | null;
  hr_coverage_percent: number | null;
  shadow_available: boolean;
  zones: ActivityZoneSummary[];
  hrmod_zones: ActivityHrmodZoneSummary[];
  zone_visualization_source: "hrmod_final" | "canonical_raw" | "none";
}

export interface ActivityWeekSummary {
  week_start: string;
  week_end: string;
  activities_count: number;
  duration_min: number;
  distance_m: number;
  canonical_training_load: number;
  zones: ActivityZoneSummary[];
}

export interface ActivityCalendar {
  schema_version: "activity-calendar-index-v1";
  athlete_id: string;
  period_start: string;
  period_end: string;
  activities: ActivityCalendarItem[];
  weeks: ActivityWeekSummary[];
  wellness_days: DailyWellnessSummary[];
  wellness_integration: "DIAGNOSTIC_ONLY";
  includes_timeseries: false;
}

export interface ActivityDetail extends ActivityCalendarItem {
  schema_version: "activity-detail-v1";
  description: string | null;
  moving_time_min: number | null;
  elapsed_time_min: number | null;
  recording_time_min: number | null;
  intervals: Array<Record<string, unknown>>;
  previous_activity_ref: string | null;
  next_activity_ref: string | null;
}

export interface ActivitySeriesPoint {
  timestamp: string | null;
  elapsed_s: number | null;
  hr_bpm: number | null;
  speed_kmh: number | null;
  altitude_m: number | null;
  grade_pct: number | null;
  quality_flags: string[];
}

export interface ActivitySeries {
  schema_version: "activity-series-v1";
  activity_ref: string;
  source_sample_count: number;
  returned_sample_count: number;
  downsample_step: number;
  series: ActivitySeriesPoint[];
}

const zones = new Set<ActivityZone>(["Z1", "Z2", "Z3", "Z4", "Z5"]);
const qualities = new Set<ActivityQuality>(["valid", "limited", "excluded", "provider_missing"]);
const isObject = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null;

function assertActivityItem(value: unknown): asserts value is ActivityCalendarItem {
  if (!isObject(value) || typeof value.activity_ref !== "string" || !/^act_[a-f0-9]{32}$/.test(value.activity_ref))
    throw new Error("API услугата върна невалидна активност.");
  if (typeof value.local_date !== "string" || typeof value.start_at_utc !== "string" || typeof value.start_local !== "string")
    throw new Error("API услугата върна невалидно време на активността.");
  if (typeof value.sport !== "string" || !qualities.has(value.quality_status as ActivityQuality))
    throw new Error("API услугата върна невалиден activity summary.");
  if (!Array.isArray(value.zones) || !value.zones.every((zone) => isObject(zone) && zones.has(zone.zone as ActivityZone)))
    throw new Error("API услугата върна невалидни activity зони.");
  const hrmodZones = Array.isArray(value.hrmod_zones) ? value.hrmod_zones : [];
  value.hrmod_zones = hrmodZones;
  if (!hrmodZones.every((zone) => isObject(zone) && zones.has(zone.zone as ActivityZone)))
    throw new Error("API услугата върна невалидни HRmod зони.");
  if (!(value.zone_visualization_source === "hrmod_final" || value.zone_visualization_source === "canonical_raw" || value.zone_visualization_source === "none"))
    value.zone_visualization_source = hrmodZones.length ? "hrmod_final" : value.zones.length ? "canonical_raw" : "none";
}

export function parseActivityCalendar(value: unknown): ActivityCalendar {
  if (!isObject(value) || value.schema_version !== "activity-calendar-index-v1" || value.includes_timeseries !== false || !Array.isArray(value.activities) || !Array.isArray(value.weeks))
    throw new Error("API услугата върна невалиден календар на активностите.");
  value.activities.forEach(assertActivityItem);
  if (!Array.isArray(value.wellness_days)) value.wellness_days = [];
  if (value.wellness_integration !== "DIAGNOSTIC_ONLY") value.wellness_integration = "DIAGNOSTIC_ONLY";
  return value as unknown as ActivityCalendar;
}

export function parseActivityDetail(value: unknown): ActivityDetail {
  assertActivityItem(value);
  if (!isObject(value) || value.schema_version !== "activity-detail-v1" || !Array.isArray(value.intervals))
    throw new Error("API услугата върна невалиден activity detail.");
  return value as unknown as ActivityDetail;
}

export function parseActivitySeries(value: unknown): ActivitySeries {
  if (!isObject(value) || value.schema_version !== "activity-series-v1" || !Array.isArray(value.series))
    throw new Error("API услугата върна невалидни графични серии.");
  return value as unknown as ActivitySeries;
}

const zoneRows = (minutes: [number, number, number, number, number]): ActivityZoneSummary[] =>
  minutes.map((value, index) => ({ zone: `Z${index + 1}` as ActivityZone, raw_time_s: value * 60, equivalent_time_s: value * 48, effective_load: value * (0.7 + index * 0.12) }));

export const activityCalendarFixture: ActivityCalendar = {
  schema_version: "activity-calendar-index-v1",
  athlete_id: "A",
  period_start: "2026-06-01",
  period_end: "2026-06-28",
  includes_timeseries: false,
  wellness_integration: "DIAGNOSTIC_ONLY",
  wellness_days: [
    { date: "2026-06-02", metrics: { sleep_duration: { value: 28800, unit: "s" }, sleep_score: { value: 86, unit: "score" }, resting_hr: { value: 41, unit: "bpm" }, hrv: { value: 92, unit: "ms" }, weight: { value: 68.7, unit: "kg" }, steps: { value: 12480, unit: "count" } } },
    { date: "2026-06-05", metrics: { sleep_duration: { value: 26400, unit: "s" }, resting_hr: { value: 44, unit: "bpm" }, hrv: { value: 78, unit: "ms" }, readiness: { value: 73, unit: "score" } } },
    { date: "2026-06-18", metrics: { sleep_duration: { value: 30000, unit: "s" }, sleep_quality: { value: 2, unit: "score" }, resting_hr: { value: 39, unit: "bpm" }, hrv: { value: 101, unit: "ms" }, weight: { value: 68.4, unit: "kg" } } },
  ],
  activities: [
    ["act_11111111111111111111111111111111", "2026-06-02", "07:35", "Run", "Леко бягане и ускорения", 54, 11200, 138, 43, "valid", true, [34, 16, 4, 0, 0]],
    ["act_22222222222222222222222222222222", "2026-06-05", "16:10", "NordicSki", "Ролкови ски · основна тренировка", 92, 22700, 146, 78, "valid", true, [18, 46, 21, 7, 0]],
    ["act_33333333333333333333333333333333", "2026-06-05", "09:00", "WeightTraining", "Силова тренировка", 38, null, null, 38, "valid", false, [0, 0, 0, 0, 0]],
    ["act_44444444444444444444444444444444", "2026-06-09", "08:05", "TrailRun", "Изкачване към хижата", 126, 18400, 151, 106, "limited", true, [28, 51, 31, 12, 0]],
    ["act_55555555555555555555555555555555", "2026-06-13", "10:30", "Ride", "Възстановително колело", 76, 31800, 122, 42, "valid", false, [61, 15, 0, 0, 0]],
    ["act_66666666666666666666666666666666", "2026-06-18", "07:20", "Run", "4 × 8 минути праг", 68, 14600, 158, 89, "valid", true, [12, 18, 25, 13, 0]],
    ["act_77777777777777777777777777777777", "2026-06-20", "08:00", "NordicSki", "Ролкови ски · продължително", 118, 29400, 144, 96, "valid", true, [26, 58, 28, 6, 0]],
    ["act_88888888888888888888888888888888", "2026-06-24", "17:45", "Run", "Кратко възстановяване", 36, 7200, 128, 24, "excluded", false, [0, 0, 0, 0, 0]],
  ].map(([ref, day, time, sport, name, duration, distance, averageHr, load, quality, shadow, zoneMinutes]) => {
    const canonicalZones = zoneRows(zoneMinutes as [number, number, number, number, number]);
    const hasHrZones = canonicalZones.some((zone) => zone.raw_time_s > 0);
    return {
    activity_ref: ref as string,
    start_at_utc: `${day}T${time}:00+00:00`, start_local: `${day}T${time}:00`, local_date: day as string, local_time: time as string,
    timezone: "Europe/Sofia", utc_offset_minutes: 180, sport: sport as string, activity_type: sport as string, activity_sub_type: null, name: name as string,
    duration_min: duration as number, distance_m: distance as number | null, elevation_gain_m: sport === "TrailRun" ? 920 : sport === "NordicSki" ? 410 : 90,
    average_hr_bpm: averageHr as number | null, max_hr_bpm: averageHr ? Number(averageHr) + 24 : null,
    average_speed_mps: distance ? Number(distance) / (Number(duration) * 60) : null, max_speed_mps: null,
    canonical_training_load: load as number, quality_status: quality as ActivityQuality,
    quality_reason: quality === "limited" ? "Ограничено HR покритие." : quality === "excluded" ? "Недостатъчно надеждни HR данни." : null,
    hr_coverage_percent: quality === "limited" ? 71 : quality === "excluded" ? 18 : sport === "WeightTraining" ? 0 : 98,
    shadow_available: shadow as boolean,
    zones: canonicalZones,
    hrmod_zones: shadow && hasHrZones ? canonicalZones.map((zone, index) => ({ zone: zone.zone, final_time_s: Math.max(0, zone.raw_time_s + (index === 0 ? -120 : index === 1 ? 120 : 0)) })) : [],
    zone_visualization_source: shadow && hasHrZones ? "hrmod_final" as const : hasHrZones ? "canonical_raw" as const : "none" as const,
  }; }),
  weeks: [],
};

const detailFixture = activityCalendarFixture.activities[5];
export const activityDetailFixture: ActivityDetail = {
  ...detailFixture, schema_version: "activity-detail-v1",
  description: "Контролирана прагова работа. Добро усещане, последната серия беше най-стабилна.",
  moving_time_min: 66, elapsed_time_min: 68, recording_time_min: 68,
  intervals: [
    { name: "Загряване", elapsed_time_s: 900, distance_m: 2700, average_hr_bpm: 132 },
    ...[1, 2, 3, 4].map((index) => ({ name: `Интервал ${index}`, elapsed_time_s: 480, distance_m: 2050 + index * 30, average_hr_bpm: 164 + index })),
  ],
  previous_activity_ref: activityCalendarFixture.activities[4].activity_ref,
  next_activity_ref: activityCalendarFixture.activities[6].activity_ref,
};

export const activitySeriesFixture: ActivitySeries = {
  schema_version: "activity-series-v1", activity_ref: detailFixture.activity_ref,
  source_sample_count: 4080, returned_sample_count: 137, downsample_step: 30,
  series: Array.from({ length: 137 }, (_, index) => {
    const elapsed = index * 30;
    const work = [900, 1560, 2220, 2880].some((start) => elapsed >= start && elapsed < start + 480);
    return {
      timestamp: new Date(Date.parse(detailFixture.start_at_utc) + elapsed * 1000).toISOString(), elapsed_s: elapsed,
      hr_bpm: work ? 158 + 10 * Math.sin(index / 4) : 124 + 12 * Math.sin(index / 8),
      speed_kmh: work ? 15.2 + 1.2 * Math.sin(index / 3) : 10.1 + 0.8 * Math.sin(index / 5),
      altitude_m: 612 + index * 0.45 + 8 * Math.sin(index / 12), grade_pct: 1.8 * Math.sin(index / 6), quality_flags: [],
    };
  }),
};
