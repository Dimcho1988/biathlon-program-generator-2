import { exactKeys, isCalendarDate, isRecord } from "./training-status";

export const PLANNING_EVENT_TYPES = [
  "MAIN_RACE",
  "CONTROL_RACE",
  "CAMP",
  "TEST",
  "UNAVAILABLE",
] as const;
export type PlanningEventType = typeof PLANNING_EVENT_TYPES[number];

export interface RaceCourse {
  distance_m: number;
  surface: "TRACK" | "ROAD" | "CROSS" | "TRAIL" | "SNOW";
  profile: "FLAT" | "UPHILL" | "ROLLING";
  gradient_percent?: [number, number];
  ascent_m?: number;
  descent_m?: number;
  expected_duration_min?: [number, number];
  specific_sessions_percent?: number;
  familiar_terrain?: boolean;
}

export interface PlanningCalendarEvent {
  event_id: string;
  event_type: PlanningEventType;
  name: string;
  start_date: string;
  end_date: string;
  race_course?: RaceCourse;
}

export interface PlanningCalendar {
  schema_version: "planning-calendar-v1";
  events: PlanningCalendarEvent[];
}

export type PlanningMissingInput =
  | "PLANNING_PROFILE"
  | "MESOCYCLE_ACCENTS"
  | "FUTURE_MAIN_RACE"
  | "TRAINING_SNAPSHOT";

export interface PlanningGenerationContext {
  schema_version: "planning-context-v1";
  as_of: string;
  ready_for_generation: boolean;
  generator_status: "NOT_ACTIVE";
  missing_inputs: PlanningMissingInput[];
  next_main_race: PlanningCalendarEvent | null;
  methodology_version: "onflows-canonical-v1";
  recovery_basis: "LOAD_ONLY";
  wellness_integration: "DIAGNOSTIC_ONLY";
}

export interface PlanningCalendarResponse {
  configured: boolean;
  calendar: PlanningCalendar | null;
  context: PlanningGenerationContext;
}

const calendarKeys = ["schema_version", "events"];
const eventKeys = ["event_id", "event_type", "name", "start_date", "end_date"];
const responseKeys = ["configured", "calendar", "context"];
const contextKeys = [
  "schema_version",
  "as_of",
  "ready_for_generation",
  "generator_status",
  "missing_inputs",
  "next_main_race",
  "methodology_version",
  "recovery_basis",
  "wellness_integration",
];
const missingInputOrder: PlanningMissingInput[] = [
  "PLANNING_PROFILE",
  "MESOCYCLE_ACCENTS",
  "FUTURE_MAIN_RACE",
  "TRAINING_SNAPSHOT",
];

export function parseRaceCourse(value: unknown): RaceCourse {
  const keys = ["distance_m", "surface", "profile", "gradient_percent", "ascent_m", "descent_m", "expected_duration_min", "specific_sessions_percent", "familiar_terrain"];
  const number = (v: unknown, lo: number, hi: number) => typeof v === "number" && Number.isFinite(v) && v >= lo && v <= hi;
  const pair = (v: unknown, lo: number, hi: number) => v === undefined || (Array.isArray(v) && v.length === 2 && v.every(n => number(n, lo, hi)) && v[0] <= v[1]);
  if (!isRecord(value) || Object.keys(value).some(k => !keys.includes(k))
    || !number(value.distance_m, 50, 1_000_000)
    || typeof value.surface !== "string" || !["TRACK", "ROAD", "CROSS", "TRAIL", "SNOW"].includes(value.surface)
    || typeof value.profile !== "string" || !["FLAT", "UPHILL", "ROLLING"].includes(value.profile)
    || !pair(value.gradient_percent, .1, 60) || !pair(value.expected_duration_min, .1, 1440)
    || (value.ascent_m !== undefined && !number(value.ascent_m, 0, 100_000))
    || (value.descent_m !== undefined && !number(value.descent_m, 0, 100_000))
    || (value.specific_sessions_percent !== undefined && !number(value.specific_sessions_percent, 0, 100))
    || (value.familiar_terrain !== undefined && typeof value.familiar_terrain !== "boolean")
    || (value.profile !== "UPHILL" && value.gradient_percent !== undefined)
    || (value.surface === "TRACK" && value.profile !== "FLAT")) throw new Error("Проверете дистанцията, условията и диапазоните на състезателното трасе.");
  return value as unknown as RaceCourse;
}

const parseEvent = (value: unknown): PlanningCalendarEvent => {
  if (
    !isRecord(value)
    || !exactKeys(value, "race_course" in value ? [...eventKeys, "race_course"] : eventKeys)
    || typeof value.event_id !== "string"
    || !/^[A-Za-z0-9][A-Za-z0-9_-]{7,63}$/.test(value.event_id)
    || !PLANNING_EVENT_TYPES.includes(value.event_type as PlanningEventType)
    || typeof value.name !== "string"
    || value.name.length < 1
    || value.name.length > 120
    || value.name.trim() !== value.name
    || !isCalendarDate(value.start_date)
    || !isCalendarDate(value.end_date)
    || value.end_date < value.start_date
  ) throw new Error("Невалидно събитие в календара за планиране.");
  if (value.race_course !== undefined) {
    if (!["MAIN_RACE", "CONTROL_RACE"].includes(String(value.event_type))) throw new Error("Състезателно трасе се задава само към състезание.");
    parseRaceCourse(value.race_course);
  }
  return value as unknown as PlanningCalendarEvent;
};

export function parsePlanningCalendarInput(value: unknown): PlanningCalendar {
  if (
    !isRecord(value)
    || !exactKeys(value, calendarKeys)
    || value.schema_version !== "planning-calendar-v1"
    || !Array.isArray(value.events)
    || value.events.length > 100
  ) throw new Error("Невалидна структура на календара за планиране.");
  const events = value.events.map(parseEvent);
  if (new Set(events.map((event) => event.event_id)).size !== events.length)
    throw new Error("Идентификаторите на календарните събития трябва да са уникални.");
  const canonical = [...events].sort((left, right) =>
    left.start_date.localeCompare(right.start_date)
    || left.end_date.localeCompare(right.end_date)
    || left.event_type.localeCompare(right.event_type)
    || left.event_id.localeCompare(right.event_id));
  if (canonical.some((event, index) => event !== events[index]))
    throw new Error("Календарните събития не са в каноничен ред.");
  return { schema_version: "planning-calendar-v1", events };
}

export function parsePlanningCalendarResponse(value: unknown): PlanningCalendarResponse {
  if (
    !isRecord(value)
    || !exactKeys(value, responseKeys)
    || typeof value.configured !== "boolean"
    || (value.configured ? value.calendar === null : value.calendar !== null)
    || !isRecord(value.context)
    || !exactKeys(value.context, contextKeys)
  ) throw new Error("Невалидна структура на planning calendar отговора.");
  const calendar = value.calendar === null ? null : parsePlanningCalendarInput(value.calendar);
  const context = value.context;
  const missingInputs = context.missing_inputs;
  if (
    context.schema_version !== "planning-context-v1"
    || !isCalendarDate(context.as_of)
    || typeof context.ready_for_generation !== "boolean"
    || context.generator_status !== "NOT_ACTIVE"
    || !Array.isArray(missingInputs)
    || new Set(missingInputs).size !== missingInputs.length
    || missingInputs.some((item, index) => item !== missingInputOrder.filter(
      (candidate) => missingInputs.includes(candidate),
    )[index])
    || context.next_main_race !== null && !isRecord(context.next_main_race)
    || context.methodology_version !== "onflows-canonical-v1"
    || context.recovery_basis !== "LOAD_ONLY"
    || context.wellness_integration !== "DIAGNOSTIC_ONLY"
    || context.ready_for_generation !== (missingInputs.length === 0)
  ) throw new Error("Невалиден контекст за готовност на планирането.");
  const nextMainRace = context.next_main_race === null
    ? null
    : parseEvent(context.next_main_race);
  if (nextMainRace !== null && nextMainRace.event_type !== "MAIN_RACE")
    throw new Error("Следващото основно състезание е невалидно.");
  return {
    configured: value.configured,
    calendar,
    context: {
      ...context,
      missing_inputs: missingInputs as PlanningMissingInput[],
      next_main_race: nextMainRace,
    } as PlanningGenerationContext,
  };
}
