import { isCalendarDate, isRecord } from "./training-status";
import type { Component } from "./training-management";

export type LearningMode = "OFF" | "SHADOW" | "CONTROL";
export type LearningConfidence = "LOW" | "MEDIUM" | "HIGH";
export interface IndividualLearningConfig {
  mode: LearningMode;
  exploration_enabled: boolean;
  max_volume_step_percent: number;
  max_intensity_step: number;
}
export interface LearningComponent {
  volume_factor: number;
  proposed_volume_factor: number;
  intensity_delta: number;
  proposed_intensity_delta: number;
  reason: string;
  confidence: LearningConfidence;
  action: "INCREASE" | "DECREASE" | "HOLD";
  evidence_count: number;
}
export interface IndividualLearningReport {
  version: string;
  mode: LearningMode;
  status: string;
  as_of: string;
  effective_from: string | null;
  expires_on: string | null;
  summary: string;
  evidence_count: number;
  confidence: LearningConfidence;
  validation: {
    status: "WARMUP" | "PASSED" | "FAILED";
    evaluated: number;
    model_mae: number | null;
    baseline_mae: number | null;
  };
  components: Record<Component, LearningComponent>;
  limitations: string[];
}

export const LEARNING_MODE_LABELS: Record<LearningMode, string> = {
  OFF: "Изключено", SHADOW: "Наблюдение", CONTROL: "Управление",
};
export const LEARNING_CONFIDENCE_LABELS: Record<LearningConfidence, string> = {
  LOW: "Ниска", MEDIUM: "Средна", HIGH: "Висока",
};
const components: Component[] = ["Z1", "Z2", "Z3", "Z4", "Z5", "STR"];
const range = (value: unknown, min: number, max: number): value is number =>
  typeof value === "number" && Number.isFinite(value) && value >= min && value <= max;
const count = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
const confidence = (value: unknown): value is LearningConfidence =>
  typeof value === "string" && ["LOW", "MEDIUM", "HIGH"].includes(value);
const mode = (value: unknown): value is LearningMode =>
  typeof value === "string" && ["OFF", "SHADOW", "CONTROL"].includes(value);

export function defaultIndividualLearning(): IndividualLearningConfig {
  return { mode: "SHADOW", exploration_enabled: true, max_volume_step_percent: 5, max_intensity_step: .02 };
}

export function parseIndividualLearningConfig(value: unknown): IndividualLearningConfig {
  if (value == null) return defaultIndividualLearning();
  if (!isRecord(value) || !mode(value.mode) || typeof value.exploration_enabled !== "boolean"
    || !range(value.max_volume_step_percent, 0, 10) || !range(value.max_intensity_step, 0, .05))
    throw new Error("Провери режима на самообучение и границите на промяната.");
  return { mode: value.mode, exploration_enabled: value.exploration_enabled,
    max_volume_step_percent: value.max_volume_step_percent, max_intensity_step: value.max_intensity_step };
}

/** Optional extension: old saved plans remain readable, invalid reports cannot imply a decision. */
export function parseIndividualLearningReport(value: unknown): IndividualLearningReport | null {
  if (value == null) return null;
  const error = () => new Error("Оценката на самообучението не може да бъде показана. Обнови програмата.");
  if (!isRecord(value) || typeof value.version !== "string" || !mode(value.mode)
    || typeof value.status !== "string" || !isCalendarDate(value.as_of)
    || !(value.effective_from == null || isCalendarDate(value.effective_from))
    || !(value.expires_on == null || isCalendarDate(value.expires_on))
    || typeof value.summary !== "string" || !count(value.evidence_count) || !confidence(value.confidence)
    || !isRecord(value.validation) || !isRecord(value.components)
    || !Array.isArray(value.limitations) || !value.limitations.every(item => typeof item === "string")) throw error();
  const validation = value.validation;
  if (!["WARMUP", "PASSED", "FAILED"].includes(String(validation.status)) || !count(validation.evaluated)
    || !(validation.model_mae == null || range(validation.model_mae, 0, Number.MAX_VALUE))
    || !(validation.baseline_mae == null || range(validation.baseline_mae, 0, Number.MAX_VALUE))) throw error();
  for (const key of components) {
    const item = value.components[key];
    if (!isRecord(item) || !range(item.volume_factor, .01, 10) || !range(item.proposed_volume_factor, .01, 10)
      || !range(item.intensity_delta, -1, 1) || !range(item.proposed_intensity_delta, -1, 1)
      || typeof item.reason !== "string" || !confidence(item.confidence) || !count(item.evidence_count)
      || !["INCREASE", "DECREASE", "HOLD"].includes(String(item.action))) throw error();
  }
  // Internal memory and model details stay out of the presentation contract.
  return {
    version: value.version, mode: value.mode, status: value.status, as_of: value.as_of,
    effective_from: value.effective_from as string | null ?? null,
    expires_on: value.expires_on as string | null ?? null,
    summary: value.summary, evidence_count: value.evidence_count, confidence: value.confidence,
    validation: { status: validation.status as IndividualLearningReport["validation"]["status"],
      evaluated: validation.evaluated, model_mae: validation.model_mae as number | null ?? null,
      baseline_mae: validation.baseline_mae as number | null ?? null },
    components: value.components as unknown as Record<Component, LearningComponent>, limitations: value.limitations as string[],
  };
}
