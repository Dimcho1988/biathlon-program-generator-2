import { exactKeys, finite, isCalendarDate, isRecord, ZONES, type Zone } from "./training-status";

export interface RecoveryHistory {
  schema_version: "recovery-history-v1";
  athlete_id: string;
  period_start: string;
  period_end: string;
  basis: "load-only";
  wellness_freshness: "fresh" | "stale" | "unknown";
  wellness_coverage_percent: number;
  wellness_diagnostics?: WellnessCoverageDiagnostics | null;
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
  strength?: {
    settings: {
      tref_min: number;
      sensitivity: number;
      tau_days: number;
      fatigue_cap: number;
    };
    current: {
      readiness_percent: number;
      residual_fatigue: number;
      days_to_practical_recovery: number;
    };
    daily: Array<{
      date: string;
      readiness_before_percent: number;
      readiness_after_percent: number;
      residual_fatigue_after: number;
      impulse: number;
      effective_load: number;
      tref_min: number;
    }>;
  } | null;
}

export type WellnessCoverageField =
  | "sleep_duration"
  | "sleep_score"
  | "sleep_quality"
  | "resting_hr"
  | "average_sleeping_hr"
  | "hrv"
  | "hrv_sdnn"
  | "readiness"
  | "respiration"
  | "spo2"
  | "fatigue"
  | "stress"
  | "mood"
  | "motivation"
  | "soreness"
  | "injury";

export interface WellnessCoverageDiagnostics {
  schema_version: "wellness-coverage-v1";
  period_start: string;
  period_end: string;
  calendar_days: number;
  records_received: number;
  days_with_any_recognized_data: number;
  daily_presence_percent: number;
  recognized_field_coverage_percent: number;
  latest_observed_date: string | null;
  freshness: "fresh" | "stale" | "unknown";
  fields: Array<{
    field: WellnessCoverageField;
    source_fields: string[];
    present_days: number;
    valid_days: number;
    invalid_days: number;
    coverage_percent: number;
  }>;
  unresolved_canonical_inputs: Array<
    "soreness_legs" | "soreness_upper" | "pain" | "illness"
  >;
  model_status: "diagnostic-only";
  affects_recovery: false;
}

const legacyRootKeys = ["schema_version", "athlete_id", "period_start", "period_end", "basis", "wellness_freshness", "wellness_coverage_percent", "model", "settings", "current", "daily"];
const wellnessRootKeys = [...legacyRootKeys, "wellness_diagnostics"];
const strengthRootKeys = [...legacyRootKeys, "strength"];
const rootKeys = [...wellnessRootKeys, "strength"];
const modelKeys = ["algorithm_version", "parameter_version", "parameter_fingerprint", "practical_full_recovery_percent"];
const settingKeys = ["zone", "tref_min", "sensitivity", "tau_days", "fatigue_cap"];
const currentKeys = ["zone", "readiness_percent", "residual_fatigue", "days_to_practical_recovery"];
const dailyKeys = ["date", "zone", "readiness_before_percent", "readiness_after_percent", "residual_fatigue_after", "impulse", "effective_load", "tref_min"];
const strengthKeys = ["settings", "current", "daily"];
const strengthSettingKeys = ["tref_min", "sensitivity", "tau_days", "fatigue_cap"];
const strengthCurrentKeys = ["readiness_percent", "residual_fatigue", "days_to_practical_recovery"];
const strengthDailyKeys = ["date", "readiness_before_percent", "readiness_after_percent", "residual_fatigue_after", "impulse", "effective_load", "tref_min"];
const diagnosticsKeys = ["schema_version", "period_start", "period_end", "calendar_days", "records_received", "days_with_any_recognized_data", "daily_presence_percent", "recognized_field_coverage_percent", "latest_observed_date", "freshness", "fields", "unresolved_canonical_inputs", "model_status", "affects_recovery"];
const coverageFieldKeys = ["field", "source_fields", "present_days", "valid_days", "invalid_days", "coverage_percent"];
const coverageFields: WellnessCoverageField[] = ["sleep_duration", "sleep_score", "sleep_quality", "resting_hr", "average_sleeping_hr", "hrv", "hrv_sdnn", "readiness", "respiration", "spo2", "fatigue", "stress", "mood", "motivation", "soreness", "injury"];
const unresolvedInputs = ["soreness_legs", "soreness_upper", "pain", "illness"];
const percentage = (value: unknown): value is number =>
  finite(value) && value >= 0 && value <= 100;
const zoneAt = (value: unknown, index: number) => value === ZONES[index];
const count = (value: unknown): value is number =>
  typeof value === "number" && Number.isInteger(value) && value >= 0;
const approximately = (left: number, right: number) => Math.abs(left - right) <= 0.011;

function zoneArray<T>(value: unknown, parser: (item: unknown, index: number) => T): T[] {
  if (!Array.isArray(value) || value.length !== ZONES.length) throw new Error("Recovery данните трябва да съдържат точно Z1–Z5.");
  return value.map(parser);
}

export function parseRecoveryHistory(value: unknown): RecoveryHistory {
  if (!isRecord(value) || ![rootKeys, wellnessRootKeys, strengthRootKeys, legacyRootKeys].some((keys) => exactKeys(value, keys))) throw new Error("Невалидна структура на recovery историята.");
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
  let wellnessDiagnostics: WellnessCoverageDiagnostics | null | undefined;
  if ("wellness_diagnostics" in value) {
    const diagnostics = value.wellness_diagnostics;
    if (diagnostics === null) wellnessDiagnostics = null;
    else {
      if (!isRecord(diagnostics) || !exactKeys(diagnostics, diagnosticsKeys)) {
        throw new Error("Невалидна wellness диагностика.");
      }
      const rawFields = diagnostics.fields;
      const rawUnresolved = diagnostics.unresolved_canonical_inputs;
      const expectedCalendarDays = isCalendarDate(diagnostics.period_start) && isCalendarDate(diagnostics.period_end)
        ? Math.round((Date.parse(`${diagnostics.period_end}T00:00:00Z`) - Date.parse(`${diagnostics.period_start}T00:00:00Z`)) / 86_400_000) + 1
        : 0;
      if (
          diagnostics.schema_version !== "wellness-coverage-v1" ||
          !isCalendarDate(diagnostics.period_start) || !isCalendarDate(diagnostics.period_end) || diagnostics.period_start > diagnostics.period_end ||
          diagnostics.period_start !== value.period_start || diagnostics.period_end !== value.period_end ||
          !count(diagnostics.calendar_days) || diagnostics.calendar_days === 0 || diagnostics.calendar_days !== expectedCalendarDays ||
          !count(diagnostics.records_received) || !count(diagnostics.days_with_any_recognized_data) ||
          diagnostics.days_with_any_recognized_data > diagnostics.calendar_days || diagnostics.records_received < diagnostics.days_with_any_recognized_data ||
          !percentage(diagnostics.daily_presence_percent) || !percentage(diagnostics.recognized_field_coverage_percent) ||
          !(diagnostics.latest_observed_date === null || (isCalendarDate(diagnostics.latest_observed_date) && diagnostics.latest_observed_date >= diagnostics.period_start && diagnostics.latest_observed_date <= diagnostics.period_end)) ||
          !(diagnostics.freshness === "fresh" || diagnostics.freshness === "stale" || diagnostics.freshness === "unknown") ||
          diagnostics.model_status !== "diagnostic-only" || diagnostics.affects_recovery !== false ||
          !Array.isArray(rawFields) || rawFields.length !== coverageFields.length ||
          !Array.isArray(rawUnresolved) || rawUnresolved.length !== unresolvedInputs.length ||
          !unresolvedInputs.every((input, index) => rawUnresolved[index] === input)) {
        throw new Error("Невалидна wellness диагностика.");
      }
      const calendarDays = diagnostics.calendar_days as number;
      const daysWithData = diagnostics.days_with_any_recognized_data as number;
      const dailyPresence = diagnostics.daily_presence_percent as number;
      const aggregateFieldCoverage = diagnostics.recognized_field_coverage_percent as number;
      const fields = rawFields.map((item, index) => {
        if (!isRecord(item) || !exactKeys(item, coverageFieldKeys) || item.field !== coverageFields[index] ||
            !Array.isArray(item.source_fields) || item.source_fields.length === 0 || !item.source_fields.every((source) => typeof source === "string" && source !== "") ||
            !count(item.present_days) || !count(item.valid_days) || !count(item.invalid_days) ||
            item.present_days > calendarDays || item.valid_days + item.invalid_days > item.present_days ||
            !percentage(item.coverage_percent) || !approximately(item.coverage_percent, 100 * item.valid_days / calendarDays)) throw new Error("Невалидно wellness покритие по поле.");
        return item as unknown as WellnessCoverageDiagnostics["fields"][number];
      });
      const expectedDailyPresence = 100 * daysWithData / calendarDays;
      const expectedFieldCoverage = 100 * fields.reduce((sum, field) => sum + field.valid_days, 0) / (calendarDays * fields.length);
      if (!approximately(dailyPresence, expectedDailyPresence) ||
          !approximately(aggregateFieldCoverage, expectedFieldCoverage)) {
        throw new Error("Невалидни агрегирани wellness проценти.");
      }
      wellnessDiagnostics = { ...diagnostics, fields } as unknown as WellnessCoverageDiagnostics;
    }
  }
  let strength: RecoveryHistory["strength"];
  if ("strength" in value) {
    if (value.strength === null) strength = null;
    else {
      const raw = value.strength;
      if (!isRecord(raw) || !exactKeys(raw, strengthKeys) || !isRecord(raw.settings) || !isRecord(raw.current)) {
        throw new Error("Невалидна recovery история за сила.");
      }
      const strengthSettings = raw.settings;
      const strengthCurrent = raw.current;
      if (!exactKeys(strengthSettings, strengthSettingKeys) || !exactKeys(strengthCurrent, strengthCurrentKeys) ||
          !strengthSettingKeys.every((key) => finite(strengthSettings[key]) && strengthSettings[key] >= 0) ||
          !percentage(strengthCurrent.readiness_percent) ||
          !strengthCurrentKeys.slice(1).every((key) => finite(strengthCurrent[key]) && strengthCurrent[key] >= 0) ||
          !Array.isArray(raw.daily) || raw.daily.length !== value.daily.length / ZONES.length) {
        throw new Error("Невалидна recovery история за сила.");
      }
      const strengthDaily = raw.daily.map((item, index) => {
        if (!isRecord(item) || !exactKeys(item, strengthDailyKeys) || !isCalendarDate(item.date) ||
            item.date !== (value.daily as Array<Record<string, unknown>>)[index * ZONES.length]?.date ||
            !percentage(item.readiness_before_percent) || !percentage(item.readiness_after_percent) ||
            !strengthDailyKeys.slice(3).every((key) => finite(item[key]) && item[key] >= 0)) {
          throw new Error("Невалиден дневен recovery ред за сила.");
        }
        return item as unknown as NonNullable<RecoveryHistory["strength"]>["daily"][number];
      });
      strength = { ...raw, settings: strengthSettings, current: strengthCurrent, daily: strengthDaily } as unknown as NonNullable<RecoveryHistory["strength"]>;
    }
  }
  return { ...value, wellness_diagnostics: wellnessDiagnostics, settings, current, daily, strength } as unknown as RecoveryHistory;
}
