import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Dashboard } from "../components/dashboard";
import { getTrainingStatus } from "../lib/api";
import { trainingStatusFixture } from "../lib/fixture";
import { parseTrainingStatus } from "../lib/training-status";

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

describe("data access", () => {
  afterEach(() => { vi.unstubAllGlobals(); delete process.env.ONFLOWS_DATA_MODE; delete process.env.ONFLOWS_API_BASE_URL; });
  it("returns an explicit API error without falling back to fixture", async () => {
    process.env.ONFLOWS_API_BASE_URL = "https://api.example.test";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("failure", { status: 503 })));
    await expect(getTrainingStatus()).rejects.toThrow("API услугата върна грешка (503)");
  });
});

describe("dashboard", () => {
  it("labels fixture mode and renders ordered zones with every required field", () => {
    const html = renderToStaticMarkup(<Dashboard data={trainingStatusFixture} mode="fixture" />);
    expect(html).toContain("Демо данни");
    expect([...html.matchAll(/id="title-(Z[1-5])"/g)].map((match) => match[1])).toEqual(["Z1", "Z2", "Z3", "Z4", "Z5"]);
    for (const label of ["Реално време", "Еквивалентно време", "Tref", "7/40", "Готовност за натоварване", "Дни до пълно възстановяване"]) expect(html).toContain(label);
    expect(html).toContain("50,9 мин"); expect(html).toContain("97,8%"); expect(html).toContain("3,5 дни");
  });
  it("does not show the demo label in API mode", () => expect(renderToStaticMarkup(<Dashboard data={trainingStatusFixture} mode="api" />)).not.toContain("Демо данни"));
});
