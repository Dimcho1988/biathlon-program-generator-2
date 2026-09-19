import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { equivalentWindow, latestTrainingDay, shiftDate } from "../lib/dashboard-periods";
import { loadHistoryFixture, trainingStatusFixture } from "../lib/fixture";
import { Dashboard } from "../components/dashboard";
import { ZONES } from "../lib/training-status";
import type { LoadHistory } from "../lib/load-history";

function history(): LoadHistory {
  const daily = Array.from({ length: 40 }, (_, i) => ZONES.map((zone) => ({ date: shiftDate("2026-09-19", -39 + i), zone, effective_load: 999, e7_daily: 999, e40_daily: 999, status_7_40: 1 }))).flat();
  const activity = (id: string, date: string, minutes: number, strength = 0) => ({ activity_ref: id, date, sport: strength ? "WeightTraining" : "Run", duration_min: minutes, strength_time_min: strength, quality_status: "valid" as const, hr_coverage_percent: 100, zones: ZONES.map((zone) => ({ zone, raw_time_min: minutes * 2, equivalent_time_min: minutes, effective_load: 999, mean_effective_hr_bpm: null, average_minute_value_percent: null })) });
  return { ...structuredClone(loadHistoryFixture), period_start: "2026-08-11", period_end: "2026-09-19", daily,
    activities: [activity("old", "2026-08-10", 900), activity("first", "2026-08-11", 10), activity("before7", "2026-09-12", 20), activity("boundary7", "2026-09-13", 30), activity("a", "2026-09-19", 40), activity("b", "2026-09-19", 50), activity("str", "2026-09-19", 60, 60), activity("future", "2026-09-20", 900)] };
}

describe("dated dashboard volume", () => {
  it("uses direct equivalent volume, counts rest days and excludes out-of-window and strength HR", () => {
    const h = history(), before = JSON.stringify(h);
    expect(equivalentWindow(h, 7).totals?.Z1).toBe(120);
    expect(equivalentWindow(h, 40).weekly?.Z1).toBe(150 / 40 * 7);
    expect(equivalentWindow(h, 7).start).toBe("2026-09-13");
    expect(JSON.stringify(h)).toBe(before);
  });
  it("labels short histories and does not manufacture zero volume for missing days", () => {
    const h = history(); h.period_start = "2026-09-18"; h.daily = h.daily.filter((r) => r.date >= h.period_start);
    const w = equivalentWindow(h, 40);
    expect(w.partial).toBe(true); expect(w.days).toBe(2); expect(w.weekly?.Z1).toBe(90 / 2 * 7);
    h.daily = h.daily.filter((r) => r.date !== "2026-09-18");
    expect(equivalentWindow(h, 40).weekly).toBeNull();
    h.daily = [];
    expect(equivalentWindow(h, 7).totals).toBeNull();
  });
  it("groups all sessions on the latest recorded day, independently of input order", () => {
    const h = history(); h.activities.reverse();
    const d = latestTrainingDay(h)!;
    expect(d.day).toBe("2026-09-19"); expect(d.activities).toHaveLength(3);
    expect(d.zones[0]).toEqual({ zone: "Z1", raw: 180, equivalent: 90 });
    expect(d.strength).toBe(60);
  });
  it("separates the training day from the current state and hides Tref in technical details", () => {
    const h = history();
    const html = renderToStaticMarkup(<Dashboard view="details" mode="fixture" data={trainingStatusFixture} loadHistory={h} />);
    const day = html.slice(html.indexOf('<section class="history-section"'), html.indexOf('<section class="zones-section"'));
    expect(day).toContain("Последен тренировъчен ден"); expect(day).toContain("180 мин"); expect(day).toContain("90 мин"); expect(day).not.toContain("Готовност за натоварване");
    const current = html.slice(html.indexOf('<section class="zones-section"'), html.indexOf('<details id="model-metadata"'));
    expect(current).toContain("120 мин"); expect(current).toContain("26,3 мин"); expect(current).not.toContain("Tref"); expect(current).not.toContain("Реално време");
    expect(html).toContain("Технически параметри · Tref");
  });
});
