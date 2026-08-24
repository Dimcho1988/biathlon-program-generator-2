import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ActivityCalendarView } from "../components/activity-calendar";
import { ActivityDetailView } from "../components/activity-detail";
import {
  activityCalendarFixture,
  activityDetailFixture,
  activitySeriesFixture,
  parseActivityCalendar,
} from "../lib/activities";

describe("completed activities calendar", () => {
  it("shows names, local time, same-day activities, summaries and stable routes", () => {
    const html = renderToStaticMarkup(<ActivityCalendarView calendar={activityCalendarFixture} />);
    expect(html).toContain("Ролкови ски · основна тренировка");
    expect(html).toContain("Силова тренировка");
    expect(html).toContain("16:10");
    expect(html).toContain("09:00");
    expect(html).toContain("Обобщение за седмицата");
    expect(html).toContain("/activities/act_22222222222222222222222222222222");
    expect(html).not.toContain("activity-001");
  });

  it("requires the calendar index to prove that it excludes timeseries", () => {
    expect(parseActivityCalendar(activityCalendarFixture).includes_timeseries).toBe(false);
    expect(() => parseActivityCalendar({ ...activityCalendarFixture, includes_timeseries: true })).toThrow(/календар/);
  });
});

describe("canonical activity detail", () => {
  it("keeps canonical metrics, private note and experimental route visibly separate", () => {
    const html = renderToStaticMarkup(<ActivityDetailView activity={activityDetailFixture} series={activitySeriesFixture} />);
    expect(html).toContain("Canonical activity");
    expect(html).toContain("Canonical load");
    expect(html).toContain("HR време по зони");
    expect(html).toContain("Лично съдържание");
    expect(html).toContain("Experimental · Raw ↔ Shadow");
    expect(html).toContain(`/activities/${activityDetailFixture.activity_ref}/shadow`);
    expect(html).toContain("Предишна активност");
    expect(html).toContain("aria-label=\"Пулс\"");
    expect(html).toContain("aria-label=\"Скорост / темпо\"");
    expect(html).toContain("aria-label=\"Височина\"");
    expect(html).toContain("aria-label=\"Наклон\"");
  });

  it("keeps the canonical summary visible when timeseries are temporarily unavailable", () => {
    const html = renderToStaticMarkup(<ActivityDetailView activity={activityDetailFixture} series={null} seriesUnavailable />);
    expect(html).toContain("Canonical load");
    expect(html).toContain("Графиките временно не са заредени");
    expect(html).toContain("HR време по зони");
  });
});
