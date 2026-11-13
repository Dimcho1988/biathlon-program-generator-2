export type RunningSpeedUnit = "pace" | "kmh";
export const RUNNING_SPEED_UNIT_KEY = "onflows-running-speed-unit-v1";

export const isRunningSport = (sport?: string | null) => /^(run|virtualrun|running|treadmill|trailrun|trailrunning)$/i.test(sport ?? "Run");
export const speedUnitLabel = (unit: RunningSpeedUnit, sport?: string | null) => isRunningSport(sport) && unit === "pace" ? "мин/км" : "км/ч";
const valid = (speed: number | null | undefined): speed is number => typeof speed === "number" && Number.isFinite(speed) && speed > 0;
export function speedValue(speed: number | null | undefined, unit: RunningSpeedUnit = "pace", sport?: string | null): string {
  if (!valid(speed)) return "—";
  if (!isRunningSport(sport) || unit === "kmh") return speed.toLocaleString("bg-BG", { maximumFractionDigits: 2 });
  const seconds = Math.round(3600 / speed);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}
export function speedText(speed: number | null | undefined, unit: RunningSpeedUnit = "pace", sport?: string | null): string {
  return valid(speed) ? `${speedValue(speed, unit, sport)} ${speedUnitLabel(unit, sport)}` : "—";
}
/** Input is ascending km/h. Pace bounds are reversed because pace is reciprocal speed. */
export function speedRangeText(range?: readonly number[] | null, unit: RunningSpeedUnit = "pace", sport?: string | null): string {
  if (!range || range.length !== 2 || !range.every(valid) || range[1] < range[0]) return "—";
  const values = isRunningSport(sport) && unit === "pace" ? [range[1], range[0]] : range;
  return `${values[0] === values[1] ? speedValue(values[0], unit, sport) : values.map(v => speedValue(v, unit, sport)).join("–")} ${speedUnitLabel(unit, sport)}`;
}
