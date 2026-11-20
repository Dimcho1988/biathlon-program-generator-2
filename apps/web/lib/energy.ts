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
  sex: "MALE" | "FEMALE";
  weight_kg: number;
  height_cm: number | null;
  birth_year: number;
  resting_kcal: number | null;
  outside_factor: number;
  outside_cho_fraction: number;
  preferences: string;
  allergies: string;
};
export type EnergyActivity = Partial<Energy> & {
  activity_ref: string;
  sport: string;
  kcal: number | null;
  duration_s: number;
  covered_seconds?: number;
  coverage_percent: number;
  timeline: (Energy & { start_s: number; seconds: number })[];
  source_seconds?: {
    measured_hr: number;
    relative_intensity: number;
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
  status: string;
  reason: string | null;
  resting_basis: string;
  targets: {
    kcal: number;
    protein_g: number;
    carbohydrate_g: number;
    fat_g: number;
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
