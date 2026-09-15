import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PlanningCalendarPanel } from "../components/planning-calendar-panel";
import { getPlanningCalendar } from "../lib/api";
import {
  parsePlanningCalendarInput,
  parsePlanningCalendarResponse,
  type PlanningCalendarResponse,
} from "../lib/planning-calendar";

const response: PlanningCalendarResponse = {
  configured: true,
  calendar: {
    schema_version: "planning-calendar-v1",
    events: [
      {
        event_id: "event-main-0001",
        event_type: "MAIN_RACE",
        name: "Основен старт",
        start_date: "2026-12-12",
        end_date: "2026-12-13",
      },
    ],
  },
  context: {
    schema_version: "planning-context-v1",
    as_of: "2026-08-19",
    ready_for_generation: true,
    generator_status: "NOT_ACTIVE",
    missing_inputs: [],
    next_main_race: {
      event_id: "event-main-0001",
      event_type: "MAIN_RACE",
      name: "Основен старт",
      start_date: "2026-12-12",
      end_date: "2026-12-13",
    },
    methodology_version: "onflows-canonical-v1",
    recovery_basis: "LOAD_ONLY",
    wellness_integration: "DIAGNOSTIC_ONLY",
  },
};

describe("planning-calendar-v1 contract", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    delete process.env.ONFLOWS_API_BASE_URL;
    delete process.env.ONFLOWS_SERVICE_TOKEN;
  });

  it("accepts canonical athlete events and rejects inferred or malformed data", () => {
    expect(parsePlanningCalendarResponse(response)).toEqual(response);
    expect(() => parsePlanningCalendarInput({
      schema_version: "planning-calendar-v1",
      events: [{ ...response.calendar?.events[0], event_type: "VIRTUAL_RACE" }],
    })).toThrow(/събитие/);
    expect(() => parsePlanningCalendarResponse({
      ...response,
      context: { ...response.context, generator_status: "ACTIVE" },
    })).toThrow(/готовност/);
  });

  it("shows readiness separately from generator activation", () => {
    const html = renderToStaticMarkup(<PlanningCalendarPanel response={response} />);
    expect(html).toContain("Готовност за генериране");
    expect(html).toContain("Входовете са готови");
    expect(html).toContain("Генератор");
    expect(html).toContain("Неактивен");
    expect(html).toContain("не създава виртуално състезание");
    expect(html).toContain('action="/api/athlete/planning-calendar"');
    expect(html).toContain("Основен старт");
  });

  it("loads one athlete calendar through server-only authentication", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ status: "ok" }))
      .mockResolvedValueOnce(Response.json(response));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getPlanningCalendar("ath-profile")).resolves.toEqual(response);

    const [url, init] = fetchMock.mock.calls[1];
    expect(String(url)).toBe("https://api.example.test/api/v2/athlete/planning-calendar");
    expect(init.headers.Authorization).toBe("Bearer server-secret");
    expect(init.headers["X-OnFlows-Athlete-Alias"]).toBe("ath-profile");
  });
});
