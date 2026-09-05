import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ActivityCalendarView } from "../components/activity-calendar";
import { ActivityDetailView } from "../components/activity-detail";
import { ErrorState } from "../components/error-state";
import {
  activityCalendarFixture,
  activityDetailFixture,
  activitySeriesFixture,
  parseActivityCalendar,
  parseActivityView,
} from "../lib/activities";
import { getActivityDetail, getActivityView } from "../lib/api";

describe("completed activities calendar", () => {
  it("shows names, local time, same-day activities, summaries and stable routes", () => {
    const html = renderToStaticMarkup(<ActivityCalendarView calendar={activityCalendarFixture} />);
    expect(html).toContain("Ролкови ски · основна тренировка");
    expect(html).toContain("Силова тренировка");
    expect(html).toContain("16:10");
    expect(html).toContain("09:00");
    expect(html).toContain("Обобщение за седмицата");
    expect(html).toContain("Wellness: сън 8:00");
    expect(html).toContain("HRV 92");
    expect(html).toContain("HRmod final · experimental");
    expect(html).toContain("HRmod");
    expect(html).toContain("не променят canonical load");
    expect(html).toContain("/activities/act_22222222222222222222222222222222");
    expect(html).not.toContain("activity-001");
  });

  it("requires the calendar index to prove that it excludes timeseries", () => {
    expect(parseActivityCalendar(activityCalendarFixture).includes_timeseries).toBe(false);
    expect(() => parseActivityCalendar({ ...activityCalendarFixture, includes_timeseries: true })).toThrow(/календар/);
  });

  it("accepts generation-aware v2 calendars while retaining v1", () => {
    const version2 = {
      ...activityCalendarFixture,
      schema_version: "activity-calendar-index-v2",
      generation_id: "gen-41",
      revision: 41,
      analysis_as_of: "2026-06-28",
      activated_at: "2026-06-28T18:00:00Z",
    };
    expect(parseActivityCalendar(version2).revision).toBe(41);
    expect(() => parseActivityCalendar({ ...version2, revision: 0 })).toThrow(/версия на календара/);
  });

  it("explains a legacy snapshot and offers a real Intervals refresh", () => {
    const html = renderToStaticMarkup(<ActivityCalendarView calendar={{
      ...activityCalendarFixture,
      wellness_days: [],
      wellness_status: {
        state: "refresh_required",
        records_received: 0,
        stored_days: 0,
        displayed_days: 0,
        latest_observed_date: null,
      },
    }} />);
    expect(html).toContain("Обикновеният refresh на браузъра не ги изтегля");
    expect(html).toContain("Обнови wellness");
    expect(html).toContain("action=\"/api/integrations/intervals/refresh\"");
    expect(html).toContain("name=\"returnTo\"");
    expect(html).toContain("name=\"scope\" value=\"WELLNESS\"");
    expect(html).toContain("/activities?start=2026-06-01&amp;end=2026-06-28");
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

  it("points a missing canonical grade channel to the separate shadow view", () => {
    const seriesWithoutGrade = {
      ...activitySeriesFixture,
      series: activitySeriesFixture.series.map((point) => ({ ...point, grade_pct: null })),
    };
    const html = renderToStaticMarkup(<ActivityDetailView activity={activityDetailFixture} series={seriesWithoutGrade} />);
    expect(html).toContain("Наклонът не е част от canonical серията");
    expect(html).toContain(`/activities/${activityDetailFixture.activity_ref}/shadow`);
    expect(html).toContain("Experimental анализа");
  });

  it("accepts one coherent generation-pinned activity view and legacy revision zero", () => {
    const shadow = { schema_version: "activity-shadow-derived-v2", experimental: true };
    const active = {
      schema_version: "activity-view-v1",
      generation_id: "11111111-1111-4111-8111-111111111111",
      revision: 7,
      analysis_as_of: "2026-06-28",
      activated_at: "2026-06-28T18:00:00Z",
      activity: activityDetailFixture,
      series: activitySeriesFixture,
      shadow,
    };
    expect(parseActivityView(active)).toMatchObject({ revision: 7, shadow });
    expect(parseActivityView({
      ...active,
      generation_id: null,
      revision: 0,
      analysis_as_of: null,
      activated_at: null,
    }).revision).toBe(0);
    expect(() => parseActivityView({ ...active, revision: 0 })).toThrow(/версия/);
    expect(() => parseActivityView({
      ...active,
      series: { ...activitySeriesFixture, activity_ref: "act_" + "9".repeat(32) },
    })).toThrow(/серии/);
    expect(() => parseActivityView({ ...active, shadow: null })).toThrow(/shadow/);
  });

  it("keeps the canonical summary visible when timeseries are temporarily unavailable", () => {
    const html = renderToStaticMarkup(<ActivityDetailView activity={activityDetailFixture} series={null} seriesUnavailable />);
    expect(html).toContain("Canonical load");
    expect(html).toContain("Графиките временно не са заредени");
    expect(html).toContain("HR време по зони");
  });

  it("shows a working same-activity retry action outside the integration dashboard", () => {
    const retryHref = `/activities/${activityDetailFixture.activity_ref}`;
    const html = renderToStaticMarkup(
      <ErrorState message="API услугата не се събуди навреме." retryAvailable retryHref={retryHref} />,
    );
    expect(html).toContain(`href="${retryHref}"`);
    expect(html).toContain("Опитай отново");
  });
});

describe("activity API cold-start reliability", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    delete process.env.ONFLOWS_API_BASE_URL;
    delete process.env.ONFLOWS_SERVICE_TOKEN;
  });

  it("attempts the protected activity resource when the readiness probe gives a false failure", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(null, { status: 500 }))
      .mockResolvedValueOnce(Response.json(activityDetailFixture));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getActivityDetail(activityDetailFixture.activity_ref, "ath-profile"))
      .resolves.toEqual(activityDetailFixture);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(String(fetchMock.mock.calls[0][0])).toBe("https://api.example.test/health");
    expect(String(fetchMock.mock.calls[1][0])).toBe(
      `https://api.example.test/api/v2/real/activities/${activityDetailFixture.activity_ref}`,
    );
    expect(fetchMock.mock.calls[1][1].headers).toMatchObject({
      Authorization: "Bearer server-secret",
      "X-OnFlows-Athlete-Alias": "ath-profile",
    });
  });

  it("loads detail, series and shadow through one protected activity-view request", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    const activityView = {
      schema_version: "activity-view-v1",
      generation_id: "11111111-1111-4111-8111-111111111111",
      revision: 7,
      analysis_as_of: "2026-06-28",
      activated_at: "2026-06-28T18:00:00Z",
      activity: activityDetailFixture,
      series: activitySeriesFixture,
      shadow: { schema_version: "activity-shadow-derived-v2" },
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ status: "ok" }))
      .mockResolvedValueOnce(Response.json(activityView));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getActivityView(activityDetailFixture.activity_ref, "ath-profile"))
      .resolves.toMatchObject({ revision: 7, activity: activityDetailFixture });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(String(fetchMock.mock.calls[1][0])).toBe(
      `https://api.example.test/api/v2/real/activities/${activityDetailFixture.activity_ref}/view`,
    );
    expect(fetchMock.mock.calls[1][1].headers).toMatchObject({
      Authorization: "Bearer server-secret",
      "X-OnFlows-Athlete-Alias": "ath-profile",
    });
  });
});
