import { equivalentWindow, shiftDate } from "./dashboard-periods";
import type { LoadHistory } from "./load-history";
import type { SpeedLoad } from "./speed-load";
import { ZONES, type Zone } from "./training-status";

export type LoadSource = "hr" | "speed" | "compare";
export type LoadChartRow = { date: string; zone: string; ratio: number | null; effective: number | null };

export function speedHistoryStart(speed: SpeedLoad) {
  return speed.daily.reduce((first, row) => row.date < first ? row.date : first, speed.end_date);
}

// Sum direct Q, never E. Missing calendar rows remain unknown rather than zero.
export function speedEquivalentWindow(speed: SpeedLoad, days: number, lowerBound = speed.start_date) {
  const start = [shiftDate(speed.end_date, 1 - days), lowerBound, speed.start_date, speedHistoryStart(speed)].sort().at(-1)!;
  const count = Math.round((Date.parse(speed.end_date) - Date.parse(start)) / 86_400_000) + 1;
  const rows = speed.daily.filter(row => row.date >= start && row.date <= speed.end_date);
  const totals = Object.fromEntries(ZONES.map(zone => [zone, 0])) as Record<Zone, number>;
  const known = new Set<string>();
  for (const row of rows) {
    totals[row.zone as Zone] += row.equivalent_minutes;
    known.add(`${row.date}:${row.zone}`);
  }
  const complete = count > 0 && known.size === count * ZONES.length && known.size === rows.length && speed.status !== "UNAVAILABLE";
  return { start, end: speed.end_date, days: count, complete, partial: count < days,
    totals: complete ? totals : null,
    weekly: complete ? Object.fromEntries(ZONES.map(zone => [zone, totals[zone] * 7 / count])) as Record<Zone, number> : null };
}

export function loadComparison(history: LoadHistory | null, speed: SpeedLoad) {
  if (!history) return { error: "За сравнение е нужна и пулсова история." };
  if (history.period_end !== speed.end_date) return { error: "Двата отчета са към различни дати. Обнови данните от горния бутон, за да ги сравниш към една и съща дата." };
  const start = [history.period_start, speed.start_date, speedHistoryStart(speed)].sort().at(-1)!;
  if (start > speed.end_date) return { error: "Няма общ период за сравнение." };
  const aligned = { ...history, period_start: start };
  return { start, end: speed.end_date, hrShort: equivalentWindow(aligned, 7), hrLong: equivalentWindow(aligned, 40) };
}

export function heartRateChartRows(history: LoadHistory): LoadChartRow[] {
  return history.daily.map(row => ({ date: row.date, zone: row.zone, ratio: row.status_7_40, effective: row.effective_load }));
}

export function speedChartRows(speed: SpeedLoad): LoadChartRow[] {
  return speed.daily.map(row => ({ date: row.date, zone: row.zone, ratio: row.ratio_7_40,
    effective: speed.status === "UNAVAILABLE" ? null : row.effective_load }));
}
