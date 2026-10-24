export interface SpeedEquivalenceZone {
  zone: string; real_minutes: number; equivalent_minutes: number | null; baseline_equivalent_minutes: number | null;
  normalized_q: number | null; baseline_normalized_q: number | null; tref_minutes: number | null;
  reference_speed_kmh: number | null; low_kmh: number | null; high_kmh: number | null;
  status: string; reason?: string | null; upper_weight: number | null; slope_per_kmh: number | null;
  covered_minutes?: number;
  equivalent_minutes_range?: [number, number] | null;
  baseline_equivalent_minutes_range?: [number, number] | null;
  v10_provenance?: { status: string; evidence?: string; source?: string; extrapolated?: boolean | null; start_type?: string; reason?: string } | null;
}
export interface SpeedEquivalencePart {
  status: string; recorded_minutes: number; classified_minutes: number; coverage_percent: number; warnings: string[];
  zones: Array<Pick<SpeedEquivalenceZone, "zone" | "real_minutes" | "covered_minutes" | "equivalent_minutes" | "baseline_equivalent_minutes" | "equivalent_minutes_range" | "baseline_equivalent_minutes_range">>;
}
export interface SpeedEquivalenceComparison {
  version: string; role: "COMPARISON_ONLY"; parameters_version: string;
  settings: { version: string; origin: "EXPERT_DEFINED"; coefficients: Record<string, number> };
  reference_policy: string; recorded_minutes: number; classified_minutes: number; coverage_percent: number;
  zones: SpeedEquivalenceZone[]; warnings: string[];
  excluded_minutes?: number; outside_range_minutes?: number; uncovered_minutes?: number;
  status?: string; sport?: string; fallback_indicator?: string; start_types?: string[];
  parts?: Record<string, SpeedEquivalencePart>;
  passive_rest_minutes?: number; range_basis?: string; range_is_jointly_executable?: boolean;
}
const nonnegative = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value) && value >= 0;
export function isSpeedEquivalenceComparison(value: unknown): value is SpeedEquivalenceComparison {
  if (!value || typeof value !== "object") return false;
  const c = value as SpeedEquivalenceComparison;
  return typeof c.version === "string" && c.role === "COMPARISON_ONLY" && typeof c.parameters_version === "string"
    && typeof c.settings?.version === "string" && c.settings.origin === "EXPERT_DEFINED"
    && !!c.settings.coefficients && Object.values(c.settings.coefficients).every(nonnegative)
    && typeof c.reference_policy === "string" && [c.recorded_minutes, c.classified_minutes, c.coverage_percent].every(nonnegative)
    && c.coverage_percent <= 100.001 && c.classified_minutes <= c.recorded_minutes + .001
    && Array.isArray(c.warnings) && c.warnings.every(w => typeof w === "string")
    && Array.isArray(c.zones) && c.zones.length === 5 && c.zones.every((z, i) => z.zone === `Z${i + 1}` && typeof z.status === "string"
      && nonnegative(z.real_minutes) && [z.equivalent_minutes, z.baseline_equivalent_minutes, z.normalized_q, z.baseline_normalized_q,
        z.tref_minutes, z.reference_speed_kmh, z.low_kmh, z.high_kmh, z.upper_weight, z.slope_per_kmh].every(n => n === null || nonnegative(n)));
}

export function speedEquivalenceReason(reason?: string | null): string {
  if (!reason) return "";
  if (/^NARROW_RANGE_SENSITIVITY_Z[1-5]$/.test(reason)) return `${reason.slice(-2)}: тесният скоростен диапазон прави теглото чувствително към малка промяна на скоростта.`;
  const labels: Record<string, string> = {
    EXPERT_WEIGHTS_NOT_MEASURED_ENERGY: "Коефициентите са експертни настройки; не са измерени физиологични константи.",
    LOCAL_REFERENCES_NOT_CONTINUOUS_ENERGY_CURVE: "Всяка зона има собствена опора. Разликите между зоните не са скокове на реалния енергоразход.",
    SHARED_WITH_SELECTED_LOAD_LEDGER: "Същите зонални тегла се използват за избрания показател в Q/E, 7/40 и възстановяването.",
    UNSUPPORTED_SPORT: "За този спорт няма скоростен модел.",
    LEGACY_HR_AND_SPEED_LOADS_RETAINED: "Досегашните оценки по пулс и скорост са запазени отделно.",
    SPEED_TREF_NOT_MIGRATED: "Допълнителният нормализиран сравнителен индекс няма зададена опора. Еквивалентното време и основният Q/E се изчисляват отделно.",
    INCOHERENT_ZONE_BOUNDARIES: "Съседните зонални граници не съвпадат; скоростното зониране не е достатъчно надеждно за сравнение.",
    V10_EXTRAPOLATED_NOT_MEASURED: "V(10 сек) е екстраполирана от индивидуалната крива и не представлява измерен спринтов резултат.",
    V10_REQUIRED: "Липсва надеждна опора V(10 сек). Z5 остава без оценка по новия показател.",
    ABOVE_V10_UPDATE_ANCHOR: "Има измерена скорост над V(10 сек). Тя не е ограничена мълчаливо и не получава екстраполирано тегло; провери и актуализирай опората.",
    NONMONOTONE_OR_MISSING_ZONE_BOUNDARIES: "Зоналните граници липсват или не нарастват последователно; скоростното зониране е недостъпно.",
    SPEED_ZONE_PROFILE_UNAVAILABLE: "Липсва приложим индивидуален профил на скоростните зони.",
    UPPER_BOUND_REQUIRED: "Липсва положителна горна скоростна граница.",
    NONINCREASING_BOUNDS: "Горната скоростна граница не е по-висока от долната.",
    NONMONOTONE_WEIGHT_SETTINGS: "Настройките дават намаляващо тегло и не се използват.",
    NUMERICALLY_DEGENERATE_RANGE: "Диапазонът е прекалено тесен за надеждно числово изчисление.",
    VFLAT_21S_SMOOTHING_NOT_VALIDATED_FOR_10S_SPRINTS: "Vflat използва 21-секундно изглаждане, което не е валидирано за 10-секундни спринтове.",
    SHORT_Z5_BOUT_UNRESOLVED_BY_21S_VFLAT: "Кратките участъци в Z5 не получават скоростно тегло, защото 21-секундното изглаждане не запазва надеждно тяхната интензивност.",
    SPEED_RECOMPUTATION_REQUIRED: "Част от активностите са със стар анализ. Обнови данните, за да получиш съпоставими скоростни сегменти.",
    SAMPLE_GAP: "Има прекъсвания в скоростните данни; тези периоди остават непокрити.",
    INVALID_SPEED: "Липсваща или невалидна скорост; работното време не се приема за нулево натоварване.",
    INVALID_SEGMENT_DURATION: "Има сегмент с невалидна продължителност, който не участва в оценката.",
    INVALID_ZONE_BOUNDARIES: "Липсват валидни граници за еднозначно скоростно зониране.",
    OUTSIDE_ZONE_RANGE: "Скоростта е извън валидния диапазон на зоната.",
    MODEL_ESTIMATE_NOT_MEASURED_SPRINT: "V(10 сек) е моделна оценка, а не измерен спринтов резултат.",
    TEN_SECONDS_OUTSIDE_CURVE_DOMAIN: "10 секунди са извън допустимата област на наличната крива. Не е добавена измислена скоростна опора.",
    EXTERNAL_SNAPSHOT_INCOMPLETE: "Външният запис не съдържа всички данни за проверка на скоростната оценка.",
    PRESCRIBED_ZONE_DIFFERS_FROM_SPEED_ZONE: "Предписаната зона се различава от зоната при избраната скорост. Сравнението използва същото скоростно зониране като реалното изпълнение.",
    INVALID_ZONE: "Зоната не е част от поддържания скоростен профил.",
    INVALID_ZONE_BOUNDS: "Липсват валидни граници на зоната.",
    NONPOSITIVE_LOWER_BOUND: "Долната скоростна граница трябва да е положителна.",
    MISSING_V10_ANCHOR: "Липсва надеждна опора V(10 сек).",
    V10_UNAVAILABLE: "Липсва надеждна опора V(10 сек).",
    V10_EXTRAPOLATED: "V(10 сек) е екстраполирана оценка, а не измерен резултат.",
    NARROW_ZONE_RANGE: "Тесният диапазон прави теглото чувствително към малка промяна на скоростта.",
    OUTSIDE_VALIDATED_RANGE: "Има скорости извън диапазона; нужна е проверка на опорите.",
    VFLAT_SMOOTHING_NOT_SPRINT_VALIDATED: "Изглаждането на Vflat не потвърждава надеждна 10-секундна спринтова скорост.",
    RUN_ONLY: "Коефициентите се прилагат само за бягане.",
    TREF_UNAVAILABLE: "Липсва съпоставимо Tref за нормализиран Q.",
  };
  return labels[reason] ?? "Няма достатъчно надеждни данни за тази оценка.";
}

export interface PlanSpeedTarget {
  zone: string; unit: "EQUIVALENT_MINUTES_AT_ZONE_REFERENCE"; reference_speed_kmh: number | null;
  target_equivalent_minutes: number | null; planned_equivalent_minutes: number | null; actual_equivalent_minutes: number | null;
  remaining_equivalent_minutes: number | null; excess_equivalent_minutes: number | null;
  baseline_planned_equivalent_minutes: number | null; baseline_actual_equivalent_minutes: number | null;
  baseline_remaining_equivalent_minutes: number | null; target_attainment_percent: number | null;
  baseline_target_attainment_percent: number | null; reason: string | null;
}
export interface PlanSpeedComparison {
  version: string; role: "COMPARISON_ONLY"; authoritative_load_changed: false;
  start_date: string | null; end_date: string | null;
  source_plan?: { kind: "DRAFT" | "ACTIVE"; revision: number; start_date: string; fingerprint: string };
  evaluated_at?: string; actual_through?: string; stored_plan_unchanged?: boolean;
  sports: Array<{ sport: string; parameters_version: string; planned: SpeedEquivalenceComparison;
    actual: { status: string; comparison: SpeedEquivalenceComparison | null; activity_count: number; available_activity_count: number; missing_comparison_count: number; different_version_count: number; reason: string | null };
    targets: PlanSpeedTarget[] }>;
}
export interface PlanSpeedSource { kind: "DRAFT" | "ACTIVE"; revision: number; start_date?: string }
export function isPlanSpeedComparison(value: unknown): value is PlanSpeedComparison {
  if (!value || typeof value !== "object") return false;
  const p = value as PlanSpeedComparison;
  return typeof p.version === "string" && p.role === "COMPARISON_ONLY" && p.authoritative_load_changed === false
    && Array.isArray(p.sports) && p.sports.every(s => typeof s.sport === "string" && typeof s.parameters_version === "string"
      && isSpeedEquivalenceComparison(s.planned) && s.planned.parameters_version === s.parameters_version
      && !!s.actual && typeof s.actual.status === "string"
      && [s.actual.activity_count, s.actual.available_activity_count, s.actual.missing_comparison_count, s.actual.different_version_count].every(nonnegative)
      && (s.actual.comparison === null || isSpeedEquivalenceComparison(s.actual.comparison) && s.actual.comparison.parameters_version === s.parameters_version)
      && Array.isArray(s.targets) && s.targets.length === 5 && s.targets.every((r, i) => r.zone === `Z${i + 1}` && r.unit === "EQUIVALENT_MINUTES_AT_ZONE_REFERENCE"
        && [r.target_equivalent_minutes, r.planned_equivalent_minutes, r.actual_equivalent_minutes, r.remaining_equivalent_minutes,
          r.excess_equivalent_minutes, r.baseline_planned_equivalent_minutes, r.baseline_actual_equivalent_minutes,
          r.baseline_remaining_equivalent_minutes, r.target_attainment_percent, r.baseline_target_attainment_percent].every(n => n === null || nonnegative(n))));
}
export function planSpeedReason(reason?: string | null): string {
  const labels: Record<string, string> = {
    LEGACY_HR_TARGET_NOT_CONVERTED: "Дългосрочната цел е по досегашния пулсов показател; няма съгласувано преобразуване към скоростната опора.",
    TARGET_METRIC_VERSION_MISMATCH: "Целта е с различна версия или скоростна опора и не се сравнява с този отчет.",
    TARGET_UNIT_MISMATCH: "Целта и скоростното изпълнение са в различни единици.",
    INVALID_TARGET: "Няма валидна цел по този показател.",
    EXECUTION_COMPARISON_RECOMPUTATION_REQUIRED: "За част от изпълнението липсва скоростна оценка със същата версия и опори. То остава неизвестно до преизчисляване.",
    PARTIAL_EXECUTION_SPEED_COVERAGE: "Скоростната оценка покрива само част от изпълнението. Остатъкът и достигането на целта остават неизвестни.",
    PARTIAL_SPEED_COVERAGE: "Скоростната оценка покрива само част от работата; достигането на целта не е потвърдено.",
  };
  return reason ? labels[reason] ?? speedEquivalenceReason(reason) : "";
}

const record = (value: unknown): value is Record<string, unknown> => Boolean(value && typeof value === "object" && !Array.isArray(value));
const nullableRange = (value: unknown) => value === null || Array.isArray(value) && value.length === 2 && value.every(nonnegative) && value[0] <= value[1];
const SESSION_ZONE_FIELDS = ["real_minutes", "covered_minutes", "equivalent_minutes", "baseline_equivalent_minutes", "normalized_q", "baseline_normalized_q", "equivalent_minutes_range", "baseline_equivalent_minutes_range"] as const;
function referencedWarnings(indices: unknown, source: string[]): string[] {
  if (!Array.isArray(indices) || !indices.every(i => Number.isInteger(i) && i >= 0 && i < source.length)) throw new Error("Невалидни препратки към качеството на скоростните данни.");
  return indices.map(i => source[i]);
}
/** Decode lossless storage references only; no speed weights or quantities are recalculated. */
export function hydrateSessionSpeedEquivalence(value: unknown, plan?: PlanSpeedComparison): SpeedEquivalenceComparison {
  if (isSpeedEquivalenceComparison(value)) return value;
  if (!record(value) || value.storage_format !== "SESSION_TOTALS_SHARED_PARAMETERS_V1") throw new Error("Невалидна скоростна оценка на сесията.");
  const shared = plan?.sports.find(s => s.sport === value.sport && s.parameters_version === value.parameters_version)?.planned;
  if (!shared || shared.parameters_version !== value.parameters_version || shared.sport !== value.sport) throw new Error("Липсва съпоставима версия на скоростните опори на сесията.");
  if (typeof value.status !== "string" || ![value.recorded_minutes, value.classified_minutes, value.coverage_percent].every(nonnegative)
    || !Array.isArray(value.zone_values) || value.zone_values.length !== 5) throw new Error("Невалидни скоростни стойности на сесията.");
  const warnings = referencedWarnings(value.warning_indices, shared.warnings);
  const zones = value.zone_values.map((row, i) => {
    if (!Array.isArray(row) || row.length !== SESSION_ZONE_FIELDS.length || !row.slice(0, 2).every(nonnegative)
      || !row.slice(2, 6).every(n => n === null || nonnegative(n)) || !row.slice(6).every(nullableRange)) throw new Error("Невалидни зонални стойности на сесията.");
    return {...shared.zones[i], ...Object.fromEntries(SESSION_ZONE_FIELDS.map((key, j) => [key, row[j]]))};
  });
  const parts: Record<string, SpeedEquivalencePart> = {};
  if (value.parts !== undefined && !record(value.parts)) throw new Error("Невалидни части на скоростната оценка.");
  for (const [kind, part] of Object.entries(value.parts ?? {})) {
    if (!record(part) || typeof part.status !== "string" || ![part.recorded_minutes, part.classified_minutes, part.coverage_percent].every(nonnegative)
      || !Array.isArray(part.zones) || part.zones.some(z => !record(z) || !/^Z[1-5]$/.test(String(z.zone)) || !nonnegative(z.real_minutes)
        || !nonnegative(z.covered_minutes) || ![z.equivalent_minutes, z.baseline_equivalent_minutes].every(n => n === null || nonnegative(n))
        || ![z.equivalent_minutes_range, z.baseline_equivalent_minutes_range].every(nullableRange))) throw new Error("Невалидни части на скоростната оценка.");
    parts[kind] = {...part, warnings: referencedWarnings(part.warning_indices, warnings)} as unknown as SpeedEquivalencePart;
  }
  const full = {...shared, ...value, zones, warnings, parts,
    excluded_minutes: value.excluded_minutes, outside_range_minutes: value.outside_range_minutes, uncovered_minutes: value.uncovered_minutes,
    start_types: value.start_types, passive_rest_minutes: value.passive_rest_minutes};
  if (!isSpeedEquivalenceComparison(full)) throw new Error("Несъпоставима скоростна оценка на сесията.");
  return full;
}
