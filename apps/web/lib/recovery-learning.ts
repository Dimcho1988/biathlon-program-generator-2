import { isCalendarDate, isRecord } from "./training-status";
import type { LearningMode } from "./individual-learning";

export interface LearningDay { day: string; actual_q: number | null; stress: number | null; baseline: number | null }
export interface RecoveryLearningEpisode {
  id: string; start: string; load_end: string; end: string; loading_days: number;
  recovery_limit_days: number; recovery_days: number | null; recovered: boolean;
  ratio: number | null; status: string; proposed_change_percent: number; reason: string;
  performance_change_percent: number | null;
}
export interface RecoveryLearningView {
  schema_version: "recovery-learning-view-v1"; as_of: string; source_date: string | null; timezone: string | null; mode: LearningMode; status: string;
  summary: string; evidence_count: number; validation: { status: string; evaluated: number };
  series: LearningDay[]; episodes: RecoveryLearningEpisode[];
  decision: { component: string | null; status: string; proposed_change_percent: number; applied_change_percent: number; reason: string };
  observations: Record<string, number | string | null>;
  channels: { key: string; label: string; observed_days: number }[];
  limitations: string[];
}

const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const nonnegative = (value: unknown): value is number => finite(value) && value >= 0;
const count = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
const optional = (value: unknown, check: (value: unknown) => boolean) => value === null || check(value);
const percent = (value: unknown): value is number => finite(value) && value >= -20 && value <= 20;
const score = (value: unknown): value is number => finite(value) && value >= 0 && value <= 100;
const component = (value: unknown) => value === null || ["Z1", "Z2", "Z3", "Z4", "Z5", "STR"].includes(String(value));

/** The dedicated recovery permission protects these observed values. Never reuse the public plan projection. */
export function parseRecoveryLearningView(value: unknown): RecoveryLearningView {
  const fail = () => new Error("Данните за самообучението са непълни или невалидни. Обнови страницата.");
  if (!isRecord(value) || value.schema_version !== "recovery-learning-view-v1" || !isCalendarDate(value.as_of)
    || !["OFF", "SHADOW", "CONTROL"].includes(String(value.mode)) || typeof value.status !== "string"
    || typeof value.summary !== "string" || !count(value.evidence_count) || !isRecord(value.validation)
    || typeof value.validation.status !== "string" || !count(value.validation.evaluated)
    || !Array.isArray(value.series) || value.series.length > 367 || !Array.isArray(value.episodes) || value.episodes.length > 100
    || !isRecord(value.decision) || !isRecord(value.observations) || !Array.isArray(value.channels)
    || !Array.isArray(value.limitations) || !value.limitations.every(item => typeof item === "string")
    || !(value.source_date == null || isCalendarDate(value.source_date))
    || !(value.timezone == null || typeof value.timezone === "string")) throw fail();
  const asOf = value.as_of;
  const seen = new Set<string>();
  const series = value.series.map(row => {
    if (!isRecord(row) || !isCalendarDate(row.day) || row.day > asOf || seen.has(row.day)
      || !optional(row.actual_q, nonnegative) || !optional(row.stress, score) || !optional(row.baseline, score)) throw fail();
    seen.add(row.day);
    return { day: row.day, actual_q: row.actual_q as number | null, stress: row.stress as number | null, baseline: row.baseline as number | null };
  }).sort((a,b) => a.day.localeCompare(b.day));
  const episodes = value.episodes.map(row => {
    if (!isRecord(row) || typeof row.id !== "string" || !isCalendarDate(row.start) || !isCalendarDate(row.load_end)
      || !isCalendarDate(row.end) || row.start > row.load_end || row.load_end >= row.end || row.end > asOf
      || !count(row.loading_days) || row.loading_days < 7 || row.loading_days > 21 || !nonnegative(row.recovery_limit_days)
      || !optional(row.recovery_days, nonnegative) || typeof row.recovered !== "boolean" || !optional(row.ratio, nonnegative)
      || typeof row.status !== "string" || !percent(row.proposed_change_percent) || typeof row.reason !== "string"
      || !optional(row.performance_change_percent, finite) || (row.recovered && row.recovery_days === null)) throw fail();
    return { id: row.id, start: row.start, load_end: row.load_end, end: row.end, loading_days: row.loading_days,
      recovery_limit_days: row.recovery_limit_days, recovery_days: row.recovery_days, recovered: row.recovered,
      ratio: row.ratio, status: row.status, proposed_change_percent: row.proposed_change_percent, reason: row.reason,
      performance_change_percent: row.performance_change_percent } as RecoveryLearningEpisode;
  });
  const decision = value.decision;
  if (!component(decision.component) || typeof decision.status !== "string" || !percent(decision.proposed_change_percent)
    || !percent(decision.applied_change_percent) || typeof decision.reason !== "string"
    || (value.mode !== "CONTROL" && decision.applied_change_percent !== 0)) throw fail();
  const observations: RecoveryLearningView["observations"] = {};
  for (const [key, item] of Object.entries(value.observations)) {
    if (item === null || typeof item === "string" || nonnegative(item)) observations[key] = item;
  }
  const channels = value.channels.map(row => {
    if (!isRecord(row) || typeof row.key !== "string" || typeof row.label !== "string" || !count(row.observed_days)) throw fail();
    return { key: row.key, label: row.label, observed_days: row.observed_days };
  });
  return { schema_version: value.schema_version, as_of: value.as_of, source_date: value.source_date as string | null ?? null,
    timezone: value.timezone as string | null ?? null, mode: value.mode as LearningMode,
    status: value.status, summary: value.summary, evidence_count: value.evidence_count,
    validation: { status: value.validation.status, evaluated: value.validation.evaluated }, series, episodes,
    decision: { component: decision.component as string | null, status: decision.status,
      proposed_change_percent: decision.proposed_change_percent, applied_change_percent: decision.applied_change_percent, reason: decision.reason },
    observations, channels, limitations: value.limitations as string[] };
}

export const learningDate = (day: string) => `${day.slice(8,10)}.${day.slice(5,7)}.${day.slice(0,4)}`;
export const learningNumber = (value: number) => value.toLocaleString("bg-BG", { maximumFractionDigits: 2 });
export const learningChange = (value: number) => `${value > 0 ? "+" : ""}${learningNumber(Object.is(value,-0) ? 0 : value)}%`;
