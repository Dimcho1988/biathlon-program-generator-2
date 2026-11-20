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
  coverage_percent: number;
  timeline: (Energy & { start_s: number; seconds: number })[];
  source_seconds?: {
    measured_hr: number;
    relative_intensity: number;
    strength_met?: number;
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
  targets: {
    kcal: number | null;
    protein_g_per_kg: number;
    energy_kcal_per_kg: number | null;
    protein_basis: "DAILY_ENERGY_RULE" | "INCOMPLETE_DAY_FALLBACK";
    protein_g: number;
    carbohydrate_g: number | null;
    fat_g: number | null;
    allocation_note: string;
  } | null;
};
export type EnergyHistory = {
  schema_version: string;
  period_start: string;
  period_end: string;
  today: string;
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
