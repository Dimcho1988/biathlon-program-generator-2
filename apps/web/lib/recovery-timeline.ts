import type { ModelZone, RecoveryV2 } from "./models";

const DAY_MS = 86_400_000;
export const HISTORY_DAYS = 5;
export const FORECAST_DAYS = 2;
export interface RecoveryPoint { day: number; readiness: number }

export function stepRecoveryCursor(day: number, direction: -1 | 1): number {
  const step = day < 0 || (day === 0 && direction < 0) ? 1 : 1 / 24;
  return Math.max(-HISTORY_DAYS, Math.min(FORECAST_DAYS, Math.round((day + direction * step) * 24) / 24));
}

export function dateAtOffset(asOf: string, day: number): string {
  return new Date(Date.parse(`${asOf}T00:00:00Z`) + Math.floor(day) * DAY_MS).toISOString().slice(0, 10);
}

export function recoveryHistorySegments(history: RecoveryV2, zone: ModelZone): RecoveryPoint[][] {
  const origin = Date.parse(`${history.as_of}T00:00:00Z`);
  const points = history.daily.filter(row => row.zone === zone).map(row => ({
    day: (Date.parse(`${row.date}T00:00:00Z`) - origin) / DAY_MS,
    readiness: row.readiness_after_percent,
  })).filter(point => point.day >= -HISTORY_DAYS && point.day < 0);
  const current = history.current.find(row => row.zone === zone);
  // A stale projection is a current estimate, not an observed historical day.
  if (current && !history.source_stale) points.push({ day: 0, readiness: current.readiness_percent });
  points.sort((a, b) => a.day - b.day);
  const segments: RecoveryPoint[][] = [];
  for (const point of points) {
    const last = segments.at(-1);
    if (last && point.day - last.at(-1)!.day === 1) last.push(point);
    else segments.push([point]);
  }
  return segments;
}

/** Only interpolate inside supplied predictions; never extrapolate missing data. */
export function forecastReadiness(points: RecoveryPoint[], day: number): number | null {
  const exact = points.find(point => Math.abs(point.day - day) < 1e-9);
  if (exact) return exact.readiness;
  const upper = points.findIndex(point => point.day > day);
  if (upper <= 0) return null;
  const a = points[upper - 1], b = points[upper];
  return a.readiness + (b.readiness - a.readiness) * (day - a.day) / (b.day - a.day);
}

export function recoveryForecastPoints(history: RecoveryV2, zone: ModelZone): RecoveryPoint[] {
  const current = history.current.find(row => row.zone === zone);
  const source = history.forecast.filter(row => row.zone === zone && row.days > 0)
    .map(row => ({ day: row.days, readiness: row.readiness_percent })).sort((a, b) => a.day - b.day);
  if (current) source.unshift({ day: 0, readiness: current.readiness_percent });
  const end = forecastReadiness(source, FORECAST_DAYS);
  const result = source.filter(point => point.day <= FORECAST_DAYS);
  if (end !== null && result.at(-1)?.day !== FORECAST_DAYS) result.push({ day: FORECAST_DAYS, readiness: end });
  return result;
}
