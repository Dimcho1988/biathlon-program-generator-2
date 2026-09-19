import type { LoadHistory } from "./load-history";
import { ZONES, type Zone } from "./training-status";

const DAY = 86_400_000;
export const displayDate = (value: string) => new Intl.DateTimeFormat("bg-BG", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }).format(new Date(`${value}T12:00:00Z`));
export const shiftDate = (value: string, days: number) => new Date(Date.parse(`${value}T12:00:00Z`) + days * DAY).toISOString().slice(0, 10);

// Display-only aggregation of direct equivalent minutes. E and Tref are never
// used as a proxy for volume; days without a workout still count as calendar days.
export function equivalentWindow(history: LoadHistory, days: number) {
  const end = history.period_end;
  const requestedStart = shiftDate(end, 1 - days);
  const start = requestedStart < history.period_start ? history.period_start : requestedStart;
  const availableDays = Math.round((Date.parse(end) - Date.parse(start)) / DAY) + 1;
  const known = new Map<string, Set<Zone>>();
  for (const row of history.daily) {
    if (row.date < start || row.date > end) continue;
    if (!known.has(row.date)) known.set(row.date, new Set());
    known.get(row.date)!.add(row.zone);
  }
  const complete = known.size === availableDays && [...known.values()].every((zones) => ZONES.every((z) => zones.has(z)));
  const activities = history.activities.filter((a) => a.date >= start && a.date <= end);
  const totals = Object.fromEntries(ZONES.map((z) => [z, 0])) as Record<Zone, number>;
  for (const activity of activities) {
    if (activity.strength_time_min > 0) continue;
    for (const zone of activity.zones) totals[zone.zone] += zone.equivalent_time_min;
  }
  return { start, end, days: availableDays, partial: availableDays < days, complete,
    totals: complete ? totals : null,
    weekly: complete ? Object.fromEntries(ZONES.map((z) => [z, totals[z] * 7 / availableDays])) as Record<Zone, number> : null,
    limited: activities.some((a) => a.quality_status === "limited") || history.quality.excluded_activities > 0 };
}

// The aggregate contract has dates, not within-day timestamps. Show the latest
// recorded day together, rather than choosing a supposedly latest session by ID.
export function latestTrainingDay(history: LoadHistory) {
  const dates = history.activities.filter((a) => a.date >= history.period_start && a.date <= history.period_end).map((a) => a.date).sort();
  const day = dates.at(-1);
  if (!day) return null;
  const activities = history.activities.filter((a) => a.date === day);
  const zones = ZONES.map((zone) => ({ zone, raw: 0, equivalent: 0 }));
  for (const activity of activities) {
    if (activity.strength_time_min > 0) continue;
    for (const row of activity.zones) {
      const target = zones.find((z) => z.zone === row.zone)!;
      target.raw += row.raw_time_min;
      target.equivalent += row.equivalent_time_min;
    }
  }
  return { day, activities, zones, strength: activities.reduce((sum, a) => sum + a.strength_time_min, 0), limited: activities.some((a) => a.quality_status === "limited") };
}
