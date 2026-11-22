export type Energy = {
  kcal: number;
  carbohydrate_kcal: number;
  fat_kcal: number;
  protein_kcal: number;
  carbohydrate_g: number;
  fat_g: number;
  protein_g: number;
};
export type NutritionProfile = {
  effective_from: string;
  sex: "MALE" | "FEMALE" | null;
  weight_kg: number | null;
  height_cm: number | null;
  birth_year: number | null;
  resting_kcal: number | null;
  outside_factor: number;
  outside_cho_fraction: number;
  strength_met?: number;
  strength_cho_fraction?: number;
  preferences: string;
  allergies: string;
};
export type EnergyActivity = Partial<Energy> & {
  activity_ref: string;
  sport: string;
  kcal: number | null;
  duration_s: number;
  covered_seconds?: number;
  accounted_seconds?: number;
  estimated_seconds?: number;
  unknown_seconds?: number;
  paused_seconds?: number;
  method?: "HR_OR_SPEED_REGRESSION" | "STRENGTH_MET";
  met?: number;
  above_rest_kcal?: number | null;
  carbohydrate_fraction_assumption?: number;
  protein_fraction_assumption?: number;
  uncovered_reasons_seconds?: { missing_signal: number; outside_hr_range: number };
  fallback_reasons_seconds?: { missing_signal: number; outside_hr_range: number };
  fallback_met?: number;
  fallback_carbohydrate_fraction?: number;
  coverage_percent: number;
  timeline: (Energy & { start_s: number; seconds: number })[];
  source_seconds?: {
    measured_hr: number;
    relative_intensity: number;
    strength_met?: number;
    low_intensity?: number;
    bounded_hr?: number;
    rest?: number;
    pause_rest?: number;
    pace_met?: number;
    sport_met?: number;
    unknown: number;
  };
};
export type EnergyDay = {
  day: string;
  activities: EnergyActivity[];
  weight_kg: number | null;
  activity: Energy;
  outside: Energy | null;
  total: Energy | null;
  accounted: Energy;
  coverage_percent: number | null;
  estimated_activity_seconds?: number;
  unknown_activity_seconds?: number;
  status: string;
  reason: string | null;
  resting_basis: string;
  energy_sources?: {
    resting_outside_kcal: number | null;
    movement_outside_kcal: number | null;
    activity_kcal: number | null;
  };
  outside_factor?: number;
  nutrition_basis?: {
    version: string;
    status: "AVAILABLE" | "LIMITED_HISTORY" | "UNAVAILABLE";
    target_day: string;
    provisional: boolean;
    window_start: string;
    window_end: string;
    available_days: number;
    missing_days: string[];
    mean_expenditure_kcal: number | null;
    last_day_expenditure_kcal: number | null;
    deviation_kcal: number | null;
    deviation_percent: number | null;
    correction_factor: number;
    correction_kcal: number | null;
    nonprotein_carbohydrate_fraction: number | null;
    fat_energy_fraction?: number | null;
    race_loading?: boolean;
    weight_kg: number | null;
    history: (Energy & { day: string })[];
    notes: string[];
    food_preferences: { preferences: string; allergies: string };
  };
  targets: {
    kcal: number | null;
    protein_g_per_kg: number;
    energy_kcal_per_kg: number | null;
    protein_basis: "DAILY_ENERGY_RULE" | "SEVEN_DAY_ENERGY_RULE" | "INCOMPLETE_DAY_FALLBACK";
    protein_g: number;
    carbohydrate_g: number | null;
    fat_g: number | null;
    allocation_note: string;
  } | null;
  base_targets?: EnergyDay["targets"];
};
export type RacePreparation = {
  event_id: string; event_name: string; race_date: string; preview_hash: string;
  status: "PENDING" | "ACCEPTED" | "DECLINED"; revision: number;
  can_accept: boolean; reason: string | null; late: boolean;
  days: { day: string; base_targets: NonNullable<EnergyDay["targets"]>;
    targets: NonNullable<EnergyDay["targets"]>; weight_kg: number }[];
};
export type EnergyHistory = {
  schema_version: string;
  period_start: string;
  period_end: string;
  today: string;
  tomorrow?: string;
  nutrition_version?: string;
  race_preparation?: RacePreparation[];
  timezone: string;
  profile: NutritionProfile | null;
  profile_revision: number;
  profile_missing?: string[];
  profile_source_note?: string;
  profiles: (NutritionProfile & { revision: number })[];
  days: EnergyDay[];
  assumptions: string[];
  references: string[];
};
export function parseEnergy(raw: unknown): EnergyHistory {
  const r = raw as EnergyHistory;
  if (
    !r ||
    r.schema_version !== "energy-constrained-cubic-v1" ||
    !Array.isArray(r.days) ||
    !Array.isArray(r.assumptions) ||
    !Array.isArray(r.profiles)
  )
    throw new Error("Невалиден енергиен отчет.");
  for (const d of r.days) {
    if (
      typeof d.day !== "string" ||
      !Array.isArray(d.activities) ||
      !d.accounted ||
      !d.activity
    )
      throw new Error("Невалиден дневен отчет.");
  }
  return r;
}
