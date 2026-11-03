import { isCalendarDate, isRecord } from "./training-status";
import type { LearningMode } from "./individual-learning";

export const LEARNING_PREPARING_MESSAGE = "Оценката на самообучението се подготвя във фонов режим. Опитай отново след малко.";
export class LearningPreparingError extends Error {
  constructor() { super(LEARNING_PREPARING_MESSAGE); this.name = "LearningPreparingError"; }
}

export const LEARNING_COMPONENTS = ["Z1", "Z2", "Z3", "Z4", "Z5", "STR"] as const;
export type LearningComponent = typeof LEARNING_COMPONENTS[number];
export type ComponentQ = Record<LearningComponent, number | null>;
export interface LearningDay {
  day: string; actual_q: number | null; actual_q_by_component: ComponentQ;
  stress: number | null; baseline: number | null; stress_weights: Record<string, number>;
  stress_basis: "FROZEN_BLOCK" | "FIXED_PANEL" | null; stress_reference: string | null;
}
export interface ComponentLearning {
  component: LearningComponent; recent_weekly_q: number | null; reference_weekly_q: number | null;
  recent_observed_days: number; reference_observed_days: number; accent_ratio: number | null;
  evidence_count: number; isolated_changes: number; validation_status: string;
  response_status: "SUPPORTED_POSITIVE" | "SUPPORTED_NEGATIVE" | "UNCERTAIN" | "INSUFFICIENT_DATA";
  test_step_percent: number; predicted_performance_percent: number | null;
  predicted_performance_low: number | null; predicted_performance_high: number | null;
  proposed_change_percent: number | null; applied_change_percent: number | null;
  action_status: "COLLECTING" | "SELECTED" | "WAITING" | "NOT_ELIGIBLE"; reason: string;
  model_scope: LearningComponent | "GLOBAL" | null; management_basis: "POLICY_REDUCTION" | "EXPLORATION" | "MODEL_SUPPORTED" | "HOLD";
  baseline_actual_weekly_q: number | null; target_weekly_q: number | null; execution_weekly_q_ceiling: number | null;
  effective_from: string | null; expires_on: string | null;
}
export interface RecoveryLearningEpisode {
  id: string; start: string; load_end: string; end: string; loading_days: number;
  recovery_limit_days: number; recovery_days: number | null; recovered: boolean;
  ratio: number | null; status: string; proposed_change_percent: number; reason: string;
  performance_change_percent: number | null;
}
export interface RecoveryLearningView {
  schema_version: "recovery-learning-view-v1"; as_of: string; source_date: string | null; timezone: string | null; mode: LearningMode; status: string;
  summary: string; evidence_count: number; validation: { status: string; evaluated: number };
  series: LearningDay[]; episodes: RecoveryLearningEpisode[]; components: ComponentLearning[];
  decision: { component: string | null; status: string; proposed_change_percent: number; applied_change_percent: number; reason: string };
  observations: Record<string, number | string | null>;
  channels: { key: string; label: string; observed_days: number; nominal_weight: number | null }[];
  limitations: string[];
}

const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const nonnegative = (value: unknown): value is number => finite(value) && value >= 0;
const count = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
const optional = (value: unknown, check: (value: unknown) => boolean) => value === null || check(value);
const percent = (value: unknown): value is number => finite(value) && value >= -20 && value <= 20;
const score = (value: unknown): value is number => finite(value) && value >= 0 && value <= 100;
const component = (value: unknown) => value === null || LEARNING_COMPONENTS.includes(value as LearningComponent);
export const unknownComponentQ = (): ComponentQ => ({ Z1:null, Z2:null, Z3:null, Z4:null, Z5:null, STR:null });

function unknownComponentModel(component: LearningComponent): ComponentLearning {
  return { component, recent_weekly_q:null, reference_weekly_q:null, recent_observed_days:0, reference_observed_days:0,
    accent_ratio:null, evidence_count:0, isolated_changes:0, validation_status:"WARMUP", response_status:"INSUFFICIENT_DATA",
    test_step_percent:5, predicted_performance_percent:null, predicted_performance_low:null, predicted_performance_high:null,
    proposed_change_percent:null, applied_change_percent:null, action_status:"COLLECTING", model_scope:null, management_basis:"HOLD",
    baseline_actual_weekly_q:null, target_weekly_q:null, execution_weekly_q_ceiling:null, effective_from:null, expires_on:null,
    reason:"Нужни са още независими наблюдения за компонентна оценка." };
}

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
    const actualQ = unknownComponentQ();
    if (row.actual_q_by_component != null) {
      if (!isRecord(row.actual_q_by_component)) throw fail();
      for (const [key, item] of Object.entries(row.actual_q_by_component)) {
        if (!LEARNING_COMPONENTS.includes(key as LearningComponent) || !optional(item, nonnegative)) throw fail();
        actualQ[key as LearningComponent] = item as number | null;
      }
    }
    const stressWeights: Record<string,number> = {};
    if (row.stress_weights != null) {
      if (!isRecord(row.stress_weights)) throw fail();
      for (const [key,item] of Object.entries(row.stress_weights)) {
        if (!score(item)) throw fail();
        stressWeights[key] = item;
      }
      if (Object.keys(stressWeights).length && Math.abs(Object.values(stressWeights).reduce((a,b)=>a+b,0)-100)>.05) throw fail();
    }
    if (!(row.stress_basis == null || ["FROZEN_BLOCK","FIXED_PANEL"].includes(String(row.stress_basis)))
      || !(row.stress_reference == null || typeof row.stress_reference === "string")) throw fail();
    return { day: row.day, actual_q: row.actual_q as number | null, actual_q_by_component:actualQ,
      stress: row.stress as number | null, baseline: row.baseline as number | null, stress_weights:stressWeights,
      stress_basis:row.stress_basis as LearningDay["stress_basis"] ?? null, stress_reference:row.stress_reference as string | null ?? null };
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
  let components: ComponentLearning[] = LEARNING_COMPONENTS.map(unknownComponentModel);
  if (value.components !== undefined) {
    if (!Array.isArray(value.components) || value.components.length !== 6) throw fail();
    const componentNames = new Set<string>();
    components = value.components.map(row => {
      if (!isRecord(row) || !component(row.component) || row.component === null || componentNames.has(String(row.component))
        || !optional(row.recent_weekly_q, nonnegative) || !optional(row.reference_weekly_q, nonnegative)
        || !count(row.recent_observed_days) || row.recent_observed_days>7 || !count(row.reference_observed_days) || row.reference_observed_days>40
        || !optional(row.accent_ratio, nonnegative) || !count(row.evidence_count) || !count(row.isolated_changes)
        || typeof row.validation_status !== "string" || !["SUPPORTED_POSITIVE","SUPPORTED_NEGATIVE","UNCERTAIN","INSUFFICIENT_DATA"].includes(String(row.response_status))
        || row.test_step_percent !== 5 || !optional(row.predicted_performance_percent, finite)
        || !optional(row.predicted_performance_low, finite) || !optional(row.predicted_performance_high, finite)
        || !optional(row.proposed_change_percent, percent) || !optional(row.applied_change_percent, percent)
        || !["COLLECTING","SELECTED","WAITING","NOT_ELIGIBLE"].includes(String(row.action_status)) || typeof row.reason !== "string"
        || !(row.model_scope == null || row.model_scope==="GLOBAL" || component(row.model_scope))
        || !(row.management_basis == null || ["POLICY_REDUCTION","EXPLORATION","MODEL_SUPPORTED","HOLD"].includes(String(row.management_basis)))
        || ![row.baseline_actual_weekly_q,row.target_weekly_q,row.execution_weekly_q_ceiling].every(item=>item==null||nonnegative(item))
        || ![row.effective_from,row.expires_on].every(item=>item==null||isCalendarDate(item))) throw fail();
      const predicted = [row.predicted_performance_low,row.predicted_performance_percent,row.predicted_performance_high];
      if (predicted.some(item=>item!==null) && (!predicted.every(finite) || row.validation_status!=="PASSED" || row.evidence_count<8 || row.isolated_changes<2
        || Number(predicted[0])>Number(predicted[1]) || Number(predicted[1])>Number(predicted[2]) || row.model_scope==null
        || row.response_status==="INSUFFICIENT_DATA" || (row.response_status==="SUPPORTED_POSITIVE" && Number(predicted[0])<=0)
        || (row.response_status==="SUPPORTED_NEGATIVE" && Number(predicted[2])>=0)
        || (row.response_status==="UNCERTAIN" && (Number(predicted[0])>0 || Number(predicted[2])<0)))) throw fail();
      if ((row.recent_weekly_q!==null && row.recent_observed_days!==7) || (row.reference_weekly_q!==null && row.reference_observed_days!==40)
        || (row.accent_ratio!==null && (row.recent_weekly_q===null || row.reference_weekly_q===null || row.reference_weekly_q===0))
        || (row.response_status!=="INSUFFICIENT_DATA" && row.predicted_performance_percent===null)
        || (row.action_status==="SELECTED" ? row.proposed_change_percent===null || row.applied_change_percent===null : row.proposed_change_percent!==null || row.applied_change_percent!==null)
        || (row.action_status!=="SELECTED" && [row.baseline_actual_weekly_q,row.target_weekly_q,row.execution_weekly_q_ceiling,row.effective_from,row.expires_on].some(item=>item!=null))
        || (row.effective_from!=null && row.expires_on!=null && String(row.effective_from)>String(row.expires_on))
        || (value.mode!=="CONTROL" && row.applied_change_percent!==null && row.applied_change_percent!==0)) throw fail();
      componentNames.add(String(row.component));
      return { component:row.component, recent_weekly_q:row.recent_weekly_q, reference_weekly_q:row.reference_weekly_q,
        recent_observed_days:row.recent_observed_days, reference_observed_days:row.reference_observed_days, accent_ratio:row.accent_ratio,
        evidence_count:row.evidence_count, isolated_changes:row.isolated_changes, validation_status:row.validation_status,
        response_status:row.response_status, test_step_percent:row.test_step_percent, predicted_performance_percent:row.predicted_performance_percent,
        predicted_performance_low:row.predicted_performance_low, predicted_performance_high:row.predicted_performance_high,
        proposed_change_percent:row.proposed_change_percent, applied_change_percent:row.applied_change_percent,
        action_status:row.action_status, reason:row.reason, model_scope:row.model_scope ?? null,
        management_basis:row.management_basis ?? "HOLD", baseline_actual_weekly_q:row.baseline_actual_weekly_q ?? null,
        target_weekly_q:row.target_weekly_q ?? null, execution_weekly_q_ceiling:row.execution_weekly_q_ceiling ?? null,
        effective_from:row.effective_from ?? null, expires_on:row.expires_on ?? null } as ComponentLearning;
    }).sort((a,b)=>LEARNING_COMPONENTS.indexOf(a.component)-LEARNING_COMPONENTS.indexOf(b.component));
    const selected = components.filter(item=>item.action_status==="SELECTED");
    if (selected.length>1 || (selected.length===1 && (selected[0].component!==decision.component
      || selected[0].proposed_change_percent!==decision.proposed_change_percent || selected[0].applied_change_percent!==decision.applied_change_percent))) throw fail();
  }
  const observations: RecoveryLearningView["observations"] = {};
  for (const [key, item] of Object.entries(value.observations)) {
    if (item === null || typeof item === "string" || nonnegative(item)) observations[key] = item;
  }
  const channels = value.channels.map(row => {
    if (!isRecord(row) || typeof row.key !== "string" || typeof row.label !== "string" || !count(row.observed_days)
      || !(row.nominal_weight == null || score(row.nominal_weight))) throw fail();
    return { key: row.key, label: row.label, observed_days: row.observed_days, nominal_weight:row.nominal_weight as number | null ?? null };
  });
  return { schema_version: value.schema_version, as_of: value.as_of, source_date: value.source_date as string | null ?? null,
    timezone: value.timezone as string | null ?? null, mode: value.mode as LearningMode,
    status: value.status, summary: value.summary, evidence_count: value.evidence_count,
    validation: { status: value.validation.status, evaluated: value.validation.evaluated }, series, episodes, components,
    decision: { component: decision.component as string | null, status: decision.status,
      proposed_change_percent: decision.proposed_change_percent, applied_change_percent: decision.applied_change_percent, reason: decision.reason },
    observations, channels, limitations: value.limitations as string[] };
}

export const learningDate = (day: string) => `${day.slice(8,10)}.${day.slice(5,7)}.${day.slice(0,4)}`;
export const learningNumber = (value: number) => value.toLocaleString("bg-BG", { maximumFractionDigits: 2 });
export const learningChange = (value: number) => `${value > 0 ? "+" : ""}${learningNumber(Object.is(value,-0) ? 0 : value)}%`;
