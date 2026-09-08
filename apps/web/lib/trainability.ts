import { isCalendarDate, isRecord } from "./training-status";

export const TRAINABILITY_MODEL_VERSION = "trainability_rank_hrmax_v2";
export const MINIMUM_SECONDS_BY_BAND: Record<string, number> = { Z1: 420, Z2: 420, Z3: 420, Z4: 420, Z5: 300, GENERAL: 420 };

export interface IndexBand {
  name: string;
  minimum_seconds: number;
  lower_bpm: number | null;
  upper_bpm: number | null;
  hr_seconds: number;
  hr_percent: number;
  speed_seconds: number;
  mean_hrmod_bpm: number | null;
  mean_hrmax_percent: number | null;
  mean_vflat_kmh: number | null;
  index: number | null;
  valid: boolean;
  invalid_reason: string | null;
}
export interface TrainabilityIndex {
  schema_version: "trainability-index-v2";
  model_version: string;
  normalization: "percent_hrmax";
  comparison_key: string;
  source_versions: Record<string, string>;
  hrmax_bpm: number | null;
  activity_duration_s: number | null;
  minimum_activity_seconds: number;
  zone_bounds_bpm: number[];
  minimum_seconds_by_band: Record<string, number>;
  minimum_grade_pct: number;
  general_range_percent: number[];
  hr_seconds: number;
  eligible_speed_seconds: number;
  downhill_excluded_seconds: number;
  unavailable_speed_seconds: number;
  zones: IndexBand[];
  general: IndexBand;
}
export interface IndexActivity {
  activity_ref: string;
  name: string | null;
  sport: string;
  start_at_utc: string;
  local_date: string;
  index: TrainabilityIndex | null;
  unavailable_reason: string | null;
}
export interface TrainabilityHistory {
  schema_version: "trainability-history-v1";
  period_start: string;
  period_end: string;
  generation_id: string | null;
  revision: number;
  activities: IndexActivity[];
}
const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const nullableNumber = (v: unknown) => v === null || finite(v);
const bad = () => new Error("Невалиден отговор за индекса на тренираност.");

export function parseIndexBand(v: unknown): IndexBand {
  if (!isRecord(v) || typeof v.name !== "string" || typeof v.valid !== "boolean" ||
    !Object.hasOwn(MINIMUM_SECONDS_BY_BAND, v.name) || v.minimum_seconds !== MINIMUM_SECONDS_BY_BAND[v.name] ||
    !["lower_bpm", "upper_bpm", "mean_hrmod_bpm", "mean_hrmax_percent", "mean_vflat_kmh", "index"].every(k => nullableNumber(v[k])) ||
    !["hr_seconds", "hr_percent", "speed_seconds"].every(k => finite(v[k]) && v[k] >= 0) ||
    !(v.invalid_reason === null || typeof v.invalid_reason === "string") ||
    (v.valid && (!finite(v.index) || v.invalid_reason !== null || Number(v.hr_seconds) < Number(v.minimum_seconds) - 1e-9 || Number(v.speed_seconds) < Number(v.minimum_seconds) - 1e-9)) ||
    (!v.valid && (v.index !== null || v.invalid_reason === null))) throw bad();
  return v as unknown as IndexBand;
}
export function parseTrainabilityIndex(v: unknown): TrainabilityIndex | null {
  if (v === null || v === undefined) return null;
  if (isRecord(v) && v.schema_version === "trainability-index-v1" && v.model_version === "trainability_rank_v1") return null;
  if (!isRecord(v) || v.schema_version !== "trainability-index-v2" ||
    v.model_version !== TRAINABILITY_MODEL_VERSION || v.normalization !== "percent_hrmax" || typeof v.comparison_key !== "string" ||
    !isRecord(v.source_versions) || !Object.values(v.source_versions).every(x => typeof x === "string") ||
    !nullableNumber(v.hrmax_bpm) || (v.hrmax_bpm !== null && Number(v.hrmax_bpm) <= 0) ||
    !Array.isArray(v.zone_bounds_bpm) || v.zone_bounds_bpm.length !== 6 || !v.zone_bounds_bpm.every(finite) ||
    !Array.isArray(v.general_range_percent) || v.general_range_percent.join() !== "75,92" ||
    !isRecord(v.minimum_seconds_by_band) || Object.keys(v.minimum_seconds_by_band).length !== 6 ||
    !Object.entries(MINIMUM_SECONDS_BY_BAND).every(([name, seconds]) => (v.minimum_seconds_by_band as Record<string, unknown>)[name] === seconds) ||
    v.minimum_grade_pct !== -3 || v.minimum_activity_seconds !== 420 || !nullableNumber(v.activity_duration_s) ||
    !["hr_seconds", "eligible_speed_seconds", "downhill_excluded_seconds", "unavailable_speed_seconds"].every(k => finite(v[k]) && v[k] >= 0) ||
    !Array.isArray(v.zones) || v.zones.length !== 5) throw bad();
  const zones = v.zones.map(parseIndexBand);
  const general = parseIndexBand(v.general);
  if (zones.some((z, i) => z.name !== `Z${i + 1}`) || general.name !== "GENERAL" ||
    ((v.activity_duration_s === null || (v.activity_duration_s as number) < 420) && [...zones, general].some(b => b.valid))) throw bad();
  for (const band of [...zones, general]) {
    if (!band.valid) continue;
    if (!finite(v.hrmax_bpm) || !finite(band.mean_hrmod_bpm) || band.mean_hrmod_bpm <= 0 ||
      !finite(band.mean_hrmax_percent) || !finite(band.mean_vflat_kmh) || band.mean_vflat_kmh <= 0 ||
      Math.abs(band.mean_hrmax_percent - 100 * band.mean_hrmod_bpm / v.hrmax_bpm) > 1e-8 ||
      Math.abs(band.index! - band.mean_hrmax_percent / band.mean_vflat_kmh) > 1e-8) throw bad();
  }
  return { ...v, zones, general } as unknown as TrainabilityIndex;
}
export function parseTrainabilityHistory(v: unknown): TrainabilityHistory {
  if (!isRecord(v) || v.schema_version !== "trainability-history-v1" ||
    !isCalendarDate(v.period_start) || !isCalendarDate(v.period_end) ||
    !(v.generation_id === null || typeof v.generation_id === "string") ||
    !finite(v.revision) || !Number.isInteger(v.revision) || v.revision < 0 || !Array.isArray(v.activities)) throw bad();
  const activities = v.activities.map(row => {
    if (!isRecord(row) || typeof row.activity_ref !== "string" || !/^(?:act_|shadow-)[a-f0-9]{32}$/.test(row.activity_ref) ||
      !(row.name === null || typeof row.name === "string") || typeof row.sport !== "string" ||
      typeof row.start_at_utc !== "string" || !Number.isFinite(Date.parse(row.start_at_utc)) || !isCalendarDate(row.local_date) ||
      !(row.unavailable_reason === null || typeof row.unavailable_reason === "string")) throw bad();
    const index = parseTrainabilityIndex(row.index);
    return { ...row, index, unavailable_reason: row.index != null && index === null ? "REFRESH_REQUIRED" : row.unavailable_reason } as unknown as IndexActivity;
  });
  return { ...v, activities } as unknown as TrainabilityHistory;
}
export const indexNumber = (value: number | null, digits = 2) => value === null ? "—" : value.toLocaleString("bg-BG", { maximumFractionDigits: digits, minimumFractionDigits: digits });
export const indexTime = (seconds: number) => `${Math.floor(Math.round(seconds) / 60)}:${String(Math.round(seconds) % 60).padStart(2, "0")}`;
export const invalidLabel = (reason: string | null, minimumSeconds: number) => ({
  HR_TIME_BELOW_MINIMUM: `Под ${minimumSeconds / 60} мин HRmod`, SPEED_TIME_BELOW_MINIMUM: `Под ${minimumSeconds / 60} мин разпределени скорости`,
  HRMAX_MISSING: "Липсва HRmax", ZERO_SPEED: "Нулева средна скорост",
  ACTIVITY_BELOW_7MIN: "Активност под 7 мин", ACTIVITY_DURATION_MISSING: "Липсва продължителност",
}[reason ?? ""] ?? "Недостатъчно данни");

export function bandFor(activity: IndexActivity, name: string): IndexBand | null {
  return (name === "GENERAL" ? activity.index?.general : activity.index?.zones.find(z => z.name === name)) ?? null;
}
export function lineSegments(activities: IndexActivity[], name: string): IndexActivity[][] {
  const result: IndexActivity[][] = [];
  let segment: IndexActivity[] = [];
  for (const activity of activities) {
    const band = bandFor(activity, name);
    if (!band?.valid || (segment.length && segment[0].index?.comparison_key !== activity.index?.comparison_key)) {
      if (segment.length) result.push(segment);
      segment = [];
    }
    if (band?.valid) segment.push(activity);
  }
  if (segment.length) result.push(segment);
  return result;
}
