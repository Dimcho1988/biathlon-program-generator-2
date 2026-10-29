export type SpeedRole = "RAW_MOVING_SPEED" | "FLAT_EQUIVALENT";

type SpeedBasis = { speed_role?: string; speed_basis?: string; distance_basis?: string; primary_control?: string };

export function isRawMovingSpeed(value?: SpeedBasis | null): boolean {
  return value?.speed_role === "RAW_MOVING_SPEED" || value?.speed_basis === "RAW_MOVING_SPEED" || value?.distance_basis === "RAW_MOVING_SPEED";
}

export function speedBasisLabel(value?: SpeedBasis | null): string {
  if (isRawMovingSpeed(value)) return "Скорост в движение · начална оценка";
  if (value?.speed_basis === "ACTUAL") return "Реална скорост";
  return "Равнинна референция";
}

export const RAW_SPEED_NOTE = "Начална моделна оценка по скоростта в движение, без корекция за релефа. Съпоставяй я с подобни условия и контролирай усилието и техниката.";
