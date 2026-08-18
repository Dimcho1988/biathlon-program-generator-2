import { exactKeys, finite, isCalendarDate, isRecord } from "./training-status";

export const WEEKDAYS = [
  "Понеделник",
  "Вторник",
  "Сряда",
  "Четвъртък",
  "Петък",
  "Събота",
  "Неделя",
] as const;

export interface PlanningProfile {
  schema_version: "planning-profile-v1";
  season_start: string;
  season_end: string;
  annual_target_hours: number;
  sessions_per_week: number;
  rest_days: number[];
  double_session_days: number[];
  long_session_day: number;
  intensity_days: number[];
  strength_days: number[];
  max_key_sessions_per_week: number;
  mesocycle_anchor_date: string;
  mesocycle_length_weeks: number;
  camp_default_accent_limit: number;
  double_threshold_enabled: boolean;
  double_threshold_day: number;
  double_threshold_components: Array<"Z3" | "Z4">;
}

export interface PlanningProfileResponse {
  configured: boolean;
  profile: PlanningProfile | null;
}

const responseKeys = ["configured", "profile"];
const profileKeys = [
  "schema_version",
  "season_start",
  "season_end",
  "annual_target_hours",
  "sessions_per_week",
  "rest_days",
  "double_session_days",
  "long_session_day",
  "intensity_days",
  "strength_days",
  "max_key_sessions_per_week",
  "mesocycle_anchor_date",
  "mesocycle_length_weeks",
  "camp_default_accent_limit",
  "double_threshold_enabled",
  "double_threshold_day",
  "double_threshold_components",
];
const integer = (value: unknown, minimum: number, maximum: number): value is number =>
  Number.isInteger(value) && Number(value) >= minimum && Number(value) <= maximum;
const weekdays = (value: unknown): value is number[] =>
  Array.isArray(value)
  && value.every((day) => integer(day, 0, 6))
  && new Set(value).size === value.length;

export function parsePlanningProfileResponse(value: unknown): PlanningProfileResponse {
  if (!isRecord(value) || !exactKeys(value, responseKeys) || typeof value.configured !== "boolean")
    throw new Error("Невалидна структура на профила за планиране.");
  if (!value.configured) {
    if (value.profile !== null) throw new Error("Неконфигуриран planning profile съдържа стойности.");
    return { configured: false, profile: null };
  }
  const profile = value.profile;
  if (!isRecord(profile) || !exactKeys(profile, profileKeys))
    throw new Error("Невалидна структура на профила за планиране.");
  if (
    profile.schema_version !== "planning-profile-v1"
    || !isCalendarDate(profile.season_start)
    || !isCalendarDate(profile.season_end)
    || profile.season_end <= profile.season_start
    || !finite(profile.annual_target_hours)
    || profile.annual_target_hours < 50
    || profile.annual_target_hours > 1500
    || !integer(profile.sessions_per_week, 1, 14)
    || !weekdays(profile.rest_days)
    || profile.rest_days.length >= 7
    || profile.sessions_per_week > 2 * (7 - profile.rest_days.length)
    || !weekdays(profile.double_session_days)
    || !integer(profile.long_session_day, 0, 6)
    || !weekdays(profile.intensity_days)
    || !weekdays(profile.strength_days)
    || !integer(profile.max_key_sessions_per_week, 0, 8)
    || !isCalendarDate(profile.mesocycle_anchor_date)
    || !integer(profile.mesocycle_length_weeks, 2, 6)
    || !integer(profile.camp_default_accent_limit, 1, 6)
    || typeof profile.double_threshold_enabled !== "boolean"
    || !integer(profile.double_threshold_day, 0, 6)
    || !Array.isArray(profile.double_threshold_components)
    || profile.double_threshold_components.length === 0
    || new Set(profile.double_threshold_components).size !== profile.double_threshold_components.length
    || !profile.double_threshold_components.every((component) => component === "Z3" || component === "Z4")
    || (profile.double_threshold_enabled && profile.rest_days.includes(profile.double_threshold_day))
    || (profile.double_threshold_enabled && profile.max_key_sessions_per_week < 2)
  ) throw new Error("Невалидни стойности в профила за планиране.");
  return { configured: true, profile: profile as unknown as PlanningProfile };
}
