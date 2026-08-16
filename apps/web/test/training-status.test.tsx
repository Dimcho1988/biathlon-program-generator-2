import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Dashboard } from "../components/dashboard";
import { getLoadHistory, getTrainingStatus } from "../lib/api";
import { loadHistoryFixture, trainingStatusFixture } from "../lib/fixture";
import { parseLoadHistory } from "../lib/load-history";
import { parseTrainingStatus } from "../lib/training-status";
import { applyTheme, resolveInitialTheme, THEME_STORAGE_KEY } from "../components/theme-toggle";

describe("training-status-v1 contract", () => {
  it("accepts the canonical fixture", () => expect(parseTrainingStatus(trainingStatusFixture)).toEqual(trainingStatusFixture));
  it("rejects malformed and extra fields", () => {
    expect(() => parseTrainingStatus({ ...trainingStatusFixture, schema_version: "v2" })).toThrow(/версия/);
    expect(() => parseTrainingStatus({ ...trainingStatusFixture, unexpected: true })).toThrow(/структура/);
    const malformedZones = trainingStatusFixture.zones.map((zone) =>
      zone.zone === "Z1" ? { ...zone, raw_time_min: "38.4" } : zone,
    );
    expect(() => parseTrainingStatus({ ...trainingStatusFixture, zones: malformedZones })).toThrow(/зонални/);
  });
  it("requires exactly five zones in exact Z1–Z5 order", () => {
    expect(() => parseTrainingStatus({ ...trainingStatusFixture, zones: trainingStatusFixture.zones.slice(0, 4) })).toThrow(/точно Z1–Z5/);
    expect(() => parseTrainingStatus({ ...trainingStatusFixture, zones: [] })).toThrow(/точно Z1–Z5/);
    const reordered = [...trainingStatusFixture.zones];
    [reordered[0], reordered[1]] = [reordered[1], reordered[0]];
    expect(() => parseTrainingStatus({ ...trainingStatusFixture, zones: reordered })).toThrow(/неподредени/);
  });
  it("rejects impossible calendar dates", () => {
    expect(() => parseTrainingStatus({ ...trainingStatusFixture, as_of: "2026-02-31" })).toThrow(/дата/);
    expect(() => parseTrainingStatus({ ...trainingStatusFixture, as_of: "2025-02-29" })).toThrow(/дата/);
    expect(parseTrainingStatus({ ...trainingStatusFixture, as_of: "2024-02-29" }).as_of).toBe("2024-02-29");
  });
});

describe("load-history-v1 contract", () => {
  it("accepts the aggregate fixture", () => expect(parseLoadHistory(loadHistoryFixture)).toEqual(loadHistoryFixture));
  it("normalizes harmless floating-point drift at the percentage boundary", () => {
    const activities = loadHistoryFixture.activities.map((activity, index) =>
      index === 0 ? { ...activity, hr_coverage_percent: 100.00000000000001 } : activity,
    );
    expect(parseLoadHistory({ ...loadHistoryFixture, activities }).activities[0].hr_coverage_percent).toBe(100);
    expect(() => parseLoadHistory({
      ...loadHistoryFixture,
      activities: activities.map((activity, index) =>
        index === 0 ? { ...activity, hr_coverage_percent: 100.01 } : activity,
      ),
    })).toThrow(/Невалидна активност/);
  });
  it("rejects incomplete daily zone groups and provider-shaped extras", () => {
    expect(() => parseLoadHistory({ ...loadHistoryFixture, daily: loadHistoryFixture.daily.slice(0, -1) })).toThrow(/дневна история/);
    expect(() => parseLoadHistory({ ...loadHistoryFixture, provider_athlete_id: "private" })).toThrow(/структура/);
  });
});

describe("data access", () => {
  afterEach(() => { vi.unstubAllGlobals(); delete process.env.ONFLOWS_DATA_MODE; delete process.env.ONFLOWS_API_BASE_URL; delete process.env.ONFLOWS_API_RESOURCE; delete process.env.ONFLOWS_SERVICE_TOKEN; });
  it("returns an explicit API error without falling back to fixture", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("failure", { status: 503 })));
    await expect(getTrainingStatus()).rejects.toThrow("API услугата върна грешка (503)");
  });
  it("uses the protected real endpoint and server-only authorization", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_API_RESOURCE = "real";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    const fetchMock = vi.fn().mockResolvedValue(Response.json(trainingStatusFixture));
    vi.stubGlobal("fetch", fetchMock);
    await expect(getTrainingStatus()).resolves.toEqual({ data: trainingStatusFixture, mode: "api" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe("https://api.example.test/api/v2/real/training-status");
    expect(init.headers.Authorization).toBe("Bearer server-secret");
  });
  it("fails closed when real server authentication is missing", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_API_RESOURCE = "real";
    const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock);
    await expect(getTrainingStatus()).rejects.toThrow("ONFLOWS_SERVICE_TOKEN");
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it("loads the protected aggregate history without exposing the token to the browser", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    process.env.ONFLOWS_API_RESOURCE = "real";
    process.env.ONFLOWS_SERVICE_TOKEN = "server-secret";
    const fetchMock = vi.fn().mockResolvedValue(Response.json(loadHistoryFixture));
    vi.stubGlobal("fetch", fetchMock);
    await expect(getLoadHistory()).resolves.toEqual(loadHistoryFixture);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe("https://api.example.test/api/v2/real/load-history");
    expect(init.headers.Authorization).toBe("Bearer server-secret");
  });
});

describe("dashboard", () => {
  it("renders the official logo and accessible theme control without losing content", () => {
    const html = renderToStaticMarkup(<Dashboard data={trainingStatusFixture} mode="fixture" />);
    expect(html).toContain('%2Fbrand%2Fonflows-mark.png');
    expect(html).toContain('alt="onFlows лого"');
    expect(html).toContain('aria-label="Превключи светла или тъмна тема"');
    expect(html).toContain("Тренировъчен статус");
  });
  it("labels fixture mode and renders ordered zones with every required field", () => {
    const html = renderToStaticMarkup(<Dashboard data={trainingStatusFixture} mode="fixture" />);
    expect(html).toContain("Демо данни");
    expect([...html.matchAll(/id="title-(Z[1-5])"/g)].map((match) => match[1])).toEqual(["Z1", "Z2", "Z3", "Z4", "Z5"]);
    for (const label of ["Реално време", "Еквивалентно време", "Tref", "7/40", "Готовност за натоварване", "Дни до пълно възстановяване"]) expect(html).toContain(label);
    expect(html).toContain("50,9 мин"); expect(html).toContain("97,8%"); expect(html).toContain("3,5 дни");
  });
  it("does not show the demo label in API mode", () => expect(renderToStaticMarkup(<Dashboard data={trainingStatusFixture} mode="api" />)).not.toContain("Демо данни"));
  it("renders 7/40 dynamics and aggregate activity detail", () => {
    const html = renderToStaticMarkup(<Dashboard data={trainingStatusFixture} mode="fixture" loadHistory={loadHistoryFixture} />);
    expect(html).toContain("Натоварване и динамика");
    expect(html).toContain("Динамика на индекса 7/40 по зони");
    expect(html).toContain("Реално → приравнено → ефективно");
    expect(html).toContain("NordicSki");
  });
});

describe("theme preference", () => {
  it("uses system preference when no manual choice exists", () => {
    expect(resolveInitialTheme(null, true)).toBe("dark");
    expect(resolveInitialTheme(null, false)).toBe("light");
  });
  it("restores stored light and dark choices regardless of the system", () => {
    expect(resolveInitialTheme("light", true)).toBe("light");
    expect(resolveInitialTheme("dark", false)).toBe("dark");
    expect(THEME_STORAGE_KEY).toBe("onflows-theme");
  });
  it("applies manual switching and persists the choice", () => {
    const root = { dataset: {} as DOMStringMap, style: { colorScheme: "" } as CSSStyleDeclaration };
    const storage = { setItem: vi.fn() };
    applyTheme("dark", root, storage);
    expect(root.dataset.theme).toBe("dark");
    expect(root.style.colorScheme).toBe("dark");
    expect(storage.setItem).toHaveBeenCalledWith("onflows-theme", "dark");
    applyTheme("light", root, storage);
    expect(root.dataset.theme).toBe("light");
    expect(storage.setItem).toHaveBeenLastCalledWith("onflows-theme", "light");
  });
});
