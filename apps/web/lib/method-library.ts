import { isRecord } from "./training-status";
import { durationSeconds } from "./duration-format";

export type MethodRange = [number, number];
export interface MethodOverride { enabled?: boolean; speed_multiplier?: MethodRange; work_range?: MethodRange; repetitions?: MethodRange; total_capacity_ratio?: number; speed_tolerance_fraction?: number; hr_tolerance_bpm?: number }
export interface MethodPreferences { enabled: boolean; overrides: Record<string, MethodOverride> }
export interface MethodGuidance { purpose?: string; execution?: string; recovery?: string; success?: string; errors?: string; progression?: string; adjustment?: string }
export interface MethodPrescription {
  anchor: { type: string; distance_m?: number; duration_seconds?: number };
  speed_multiplier?: MethodRange;
  hrmax_fraction?: MethodRange;
  work?: { unit: "seconds" | "meters" | "capacity_fraction"; range: MethodRange };
  structure?: string;
  pace_offset?: { distance_m: number; seconds: MethodRange };
  repetitions?: MethodRange;
  recovery?: { seconds?: MethodRange; type?: string };
  sets?: { count: number; recovery_seconds?: MethodRange | number };
  phase_ranges?: Record<string, unknown>;
  pattern?: Array<Record<string, unknown>>;
  repetitions_from_budget?: boolean;
}
export interface MethodResolution {
  status: string; reason?: string; message?: string; reasons?: string[];
  anchor?: { type: string; speed_kmh?: number; distance_m?: number; duration_seconds?: number; source?: string; evidence?: Record<string, unknown> };
  target_speed_kmh?: number; target_speed_range_kmh?: MethodRange;
  speed_multiplier_range?: MethodRange; work_range?: MethodRange; work_unit?: "seconds" | "meters" | "capacity_fraction";
  hr_reference_range_bpm?: MethodRange | null; hr_reference_basis?: string; hr_role?: string;
  work_duration_seconds?: number; work_duration_range_seconds?: MethodRange;
  work_distance_m?: number; work_distance_range_m?: MethodRange;
  phase?: string; curve_source?: string; zone?: string; components?: string[];
  speed_role?: string; segments?: MethodResolution[];
  [key: string]: unknown;
}
export interface LibraryMethod {
  id: string; title: string; sports: string[]; periods: string[]; disciplines: Array<string | number>;
  source: string | Record<string, unknown>; original?: string | Record<string, unknown>;
  adaptation?: string | Record<string, unknown>; guidance: MethodGuidance;
  prescription?: MethodPrescription | null; resolution?: MethodResolution;
  automatic_selection?: boolean;
  related_method_ids?: string[]; derived_from?: string[];
  [key: string]: unknown;
}
export interface SessionMethod {
  id: string; title?: string; source?: LibraryMethod["source"]; original?: LibraryMethod["original"];
  adaptation?: LibraryMethod["adaptation"]; guidance?: MethodGuidance; prescription?: MethodPrescription | null;
  resolution?: MethodResolution; components?: SessionMethod[];
  dosing_ref?: "dose_evidence.dosing";
}
export interface MethodLibraryResponse {
  version: string; methods: LibraryMethod[]; configured: boolean; revision: number; preferences: MethodPreferences;
  profile: { sport?: string; actual_sport?: string; discipline?: string } | null;
  phase: string; phase_source?: string; today?: string; curve?: Record<string, unknown>;
}
export const METHOD_PHASES: Record<string, string> = {
  RE_ENTRY: "Вработване", GENERAL_PREPARATION: "Обща подготовка", SPECIAL_PREPARATION: "Специална подготовка",
  PRECOMPETITION: "Предсъстезателен", COMPETITION: "Състезателен", TRANSITION: "Преходен",
};
export const METHOD_SPORTS: Record<string, string> = { Run: "Бягане", NordicSki: "Ски бягане", RollerSki: "Ролкови ски" };
export function isMethodRange(value: unknown): value is MethodRange {
  return Array.isArray(value) && value.length === 2 && value.every(n => typeof n === "number" && Number.isFinite(n)) && value[0] > 0 && value[1] >= value[0];
}
export function parseMethodPreferences(value: unknown): MethodPreferences {
  if (!isRecord(value) || Object.keys(value).some(k => !["enabled", "overrides"].includes(k))
    || typeof value.enabled !== "boolean" || !isRecord(value.overrides) || Object.keys(value.overrides).length > 500)
    throw new Error("Провери настройките на базата от методи.");
  for (const [id, override] of Object.entries(value.overrides)) {
    if (!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$/.test(id) || !isRecord(override)
      || Object.keys(override).some(k => !["enabled", "speed_multiplier", "work_range", "repetitions", "total_capacity_ratio", "speed_tolerance_fraction", "hr_tolerance_bpm"].includes(k))
      || (override.enabled !== undefined && typeof override.enabled !== "boolean")) throw new Error("Невалидна корекция на метод.");
    for (const [key, minimum, maximum] of [["total_capacity_ratio", 0, 20], ["speed_tolerance_fraction", 0, .1], ["hr_tolerance_bpm", 0, 15]] as const) {
      const n = override[key];
      if (n !== undefined && (typeof n !== "number" || !Number.isFinite(n) || n <= minimum || n > maximum || (key === "hr_tolerance_bpm" && n < 1)))
        throw new Error("Провери коефициента за Tmax и допустимите отклонения.");
    }
    for (const key of ["speed_multiplier", "work_range", "repetitions"]) {
      const r = override[key];
      if (r !== undefined && (!isMethodRange(r) || (key === "repetitions" && !r.every(Number.isInteger))))
        throw new Error("Диапазоните трябва да имат положителна долна и по-голяма или равна горна граница. Повторенията са цели числа.");
    }
  }
  return value as unknown as MethodPreferences;
}
export function parseMethodLibrary(value: unknown): MethodLibraryResponse {
  if (!isRecord(value) || typeof value.version !== "string" || !Number.isSafeInteger(value.revision) || Number(value.revision) < 0
    || typeof value.configured !== "boolean" || typeof value.phase !== "string" || !Array.isArray(value.methods)
    || !value.methods.every(m => isRecord(m) && typeof m.id === "string" && typeof m.title === "string"
      && [m.sports, m.periods].every(a => Array.isArray(a) && a.every(s => typeof s === "string"))
      && Array.isArray(m.disciplines) && m.disciplines.every(s => typeof s === "string" || typeof s === "number")
      && isRecord(m.guidance) && (typeof m.source === "string" || isRecord(m.source))))
    throw new Error("Базата от методи временно не е достъпна. Опитай отново.");
  return { ...value, preferences: parseMethodPreferences(value.preferences) } as unknown as MethodLibraryResponse;
}
export function methodAnchorLabel(anchor?: { type: string; distance_m?: number; duration_seconds?: number; source?: string }): string {
  if (!anchor) return "Индивидуален работен профил";
  if (["CV", "CS"].includes(anchor.type)) return "КС · критична скорост";
  if (anchor.type === "INDIVIDUAL_ZONE") return "Индивидуален профил на компонента";
  if (anchor.type === "V5") return "V(5 мин) · максимална средна скорост за 5 мин";
  if (["MAS", "V6"].includes(anchor.type)) return "MAS · работна оценка чрез V(6 мин), не лабораторно измерване";
  if (["ANT", "AnT"].includes(anchor.type)) return anchor.source === "MEASURED" ? "AnT · въведена измерена опора" : anchor.source === "MANUAL" ? "AnT · ръчно зададена опора" : "AnT · работна оценка 0,95 × CS при липсваща зададена или измерена опора";
  if (anchor.type === "HRMAX") return "%HRmax · публикуван пулсов ориентир";
  if (anchor.type === "V_DURATION") return `V(t) · скорост за ${durationSeconds(anchor.duration_seconds)}`;
  if (["RACE_DISTANCE", "PRIMARY_RACE"].includes(anchor.type)) return anchor.distance_m ? `V${anchor.distance_m} · скорост за ${anchor.distance_m} m` : "Скорост за основната дисциплина";
  if (anchor.type === "RACE_DURATION") return "Скорост за продължителността на старта";
  return anchor.type;
}
export function rangeText(value: MethodRange | undefined, format: (n: number) => string = n => n.toLocaleString("bg-BG", { maximumFractionDigits: 1 })): string {
  if (!value || !value.every(Number.isFinite)) return "—";
  return value[0] === value[1] ? format(value[0]) : `${format(value[0])}–${format(value[1])}`;
}
export function methodWorkText(work?: MethodPrescription["work"]): string {
  return !work ? "По структурата на метода" : work.unit === "meters" ? `${rangeText(work.range)} m` : work.unit === "capacity_fraction" ? `${rangeText(work.range, n => String(Math.round(n * 100)))}% от лично Tmax` : rangeText(work.range, durationSeconds);
}
export function methodHrText(resolution?: Pick<MethodResolution, "hr_reference_range_bpm" | "hr_reference_basis" | "hr_role">): string {
  if (!resolution?.hr_reference_range_bpm) return "Нужен е индивидуален пулсов профил.";
  if (resolution.hr_role === "PRIMARY_SOURCE_PRESCRIPTION") return `${rangeText(resolution.hr_reference_range_bpm)} уд./мин · основен ориентир по публикувания %HRmax и индивидуалната настройка за максимален пулс. Не е процент от MAS.`;
  const basis = resolution.hr_reference_basis === "INDIVIDUAL_ZONE_OBSERVATION_NOT_SPEED_PREDICTION" ? "пулсов диапазон на зоната за наблюдение; извън точната скорост–пулс връзка" : "индивидуална моделна оценка";
  return `${rangeText(resolution.hr_reference_range_bpm)} уд./мин · ${basis}.${resolution.hr_role === "SECONDARY_OBSERVATION_WITH_LAG" ? " Пулсът изостава при кратките части; не ускорявай, за да го достигнеш." : " Вторичен ориентир при устойчиво усилие."}`;
}
export function methodPaceText(speed?: MethodRange): string {
  if (!isMethodRange(speed)) return "—";
  const pace = (v: number) => { const s = Math.round(3600 / v); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
  return `${rangeText([speed[1], speed[0]], pace)} мин/km`;
}
export function methodSourceText(value: unknown): string {
  if (typeof value === "string") return value;
  if (!isRecord(value)) return "";
  return Object.entries(value).filter(([, v]) => typeof v === "string" || typeof v === "number" || Array.isArray(v))
    .map(([, v]) => Array.isArray(v) ? v.filter(x => typeof x === "string" || typeof x === "number").join(", ") : String(v)).filter(Boolean).join(" · ");
}
export function methodSourceGroup(value: LibraryMethod["source"]): string {
  return typeof value === "string" ? value : typeof value.authors === "string" ? value.authors : methodSourceText(value);
}
export function methodReasonText(reason?: string): string {
  if (!reason) return "";
  const labels: Record<string, string> = {
    CONFIRMED_EXECUTION_REQUIRED: "Първо изпълни успешно предходната стъпка и потвърди реалната активност.",
    SPORT_OR_PERIOD_NOT_SUPPORTED: "Методът е за друго средство или период.",
    DISABLED_BY_PROFILE: "Изключен в индивидуалните настройки.",
    SOURCE_FAMILY_DISABLED_BY_PROFILE: "Изключен чрез настройката на оригиналния метод. Можеш да включиш тази адаптация отделно.",
    SHORT_SPEED_TEST_REQUIRED: "Нужен е приет кратък максимален тест за същото средство. Само петминутната опора не описва надеждно тази скорост.",
    SPORT_NOT_SUPPORTED: "Методът е за друго тренировъчно средство.",
    PRIMARY_DISCIPLINE_NOT_SUPPORTED: "Методът е за друга основна дисциплина.",
    PERIOD_NOT_SUPPORTED: "Методът е за друг период на подготовката.",
    SPEED_SPORT_MISMATCH: "Нужна е крива за същото тренировъчно средство.",
    NO_INDIVIDUAL_SPEED_CURVE: "Няма използваема индивидуална крива скорост–време.",
    INDIVIDUAL_METHOD_CAPACITY_REQUIRED: "Нужна е индивидуална оценка на устойчивостта за този метод. Провери тестовете и работния профил в Управление.",
    EXPLORATORY_OR_NONMAXIMAL_TESTS: "Наличните тестове са ориентировъчни или не са максимални.",
    CV_REQUIRES_ACCEPTED_SHORT_AND_LONG_TESTS: "За КС са необходими приети максимални тестове с различна кратка и дълга продължителност.",
    PRIMARY_RACE_DISTANCE_OR_DURATION_REQUIRED: "Задай разпознаваема дистанция или продължителност на основната дисциплина в профила.",
    RACE_DURATION_REQUIRED: "Липсва продължителност за състезателната опора.",
    UNSUPPORTED_OR_NON_SPEED_ANCHOR: "Опората не е скоростна. Мощност и качествено усилие не се преобразуват автоматично в скорост.",
    NO_COMPLETE_SPEED_PRESCRIPTION: "Публикуваното предписание няма напълно уточнена скоростна структура.",
    REPETITION_REACHES_CONTINUOUS_MAXIMUM: "Отсечката достига максималната устойчивост при тази скорост. Нужна е по-лека индивидуална настройка.",
    INDIVIDUAL_SPEED_ZONES_REQUIRED: "Липсват индивидуални скоростни зони за оценка на товара.",
    OUTSIDE_OBSERVED_CAPACITY_SUPPORT: "Интензивността е извън диапазона, подкрепен от приетите индивидуални тестове.",
    WORK_INTERVAL_TOO_LONG_FOR_SELECTED_DISCIPLINE: "Отсечката е прекалено дълга спрямо основната дисциплина.",
    NO_COMPLETE_REPEAT_WITHIN_METHOD_BUDGET: "Няма място за цяло повторение в работния бюджет.",
    COMPOUND_WORK_OVERRIDE_REQUIRES_SEGMENT_PRESCRIPTION: "Комбинираният метод изисква запазване на отделните си отсечки.",
  };
  return labels[reason] ?? (/^[A-Z0-9_]+$/.test(reason) ? "Нужно е методическо уточняване преди автоматичен избор." : reason);
}
export function methodStatusLabel(status?: string, reason?: string): string {
  if (status === "INCOMPATIBLE") return reason === "SPORT_NOT_SUPPORTED" ? "За друго тренировъчно средство" : reason === "PRIMARY_DISCIPLINE_NOT_SUPPORTED" ? "За друга дисциплина" : "За друг период";
  if (reason === "SHORT_SPEED_TEST_REQUIRED") return "Добави кратък максимален тест";
  if (["NO_INDIVIDUAL_SPEED_CURVE", "SPEED_SPORT_MISMATCH"].includes(reason ?? "")) return "Нужна е лична скоростна крива";
  if (status === "PROGRESSION_LOCKED") return "Изисква предходна стъпка";
  if (status === "READY") return "Готов за индивидуално дозиране";
  if (status?.includes("DISABLED")) return "Изключен за този спортист";
  if (status?.includes("REFERENCE")) return "Справочен метод";
  if (status?.includes("SPORT")) return "За друго тренировъчно средство";
  if (status?.includes("PHASE") || status?.includes("PERIOD")) return "За друг период";
  if (status?.includes("DISCIPLINE")) return "За друга дисциплина";
  return "Нуждае се от уточняване";
}
export function methodHref(id: string): string { return `/methods?method=${encodeURIComponent(id)}`; }
