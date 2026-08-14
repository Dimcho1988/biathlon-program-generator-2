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
    expect(() => parseTrainingStatus({ ...trainingStatusFixture, zones: [{ ...trainingStatusFixture.zones[0], raw_time_min: "38.4" }] })).toThrow(/зонални/);
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
    expect(html).toContain("38,4 мин"); expect(html).toContain("84,7%"); expect(html).toContain("1,1 дни");
  });
  it("does not show the demo label in API mode", () => expect(renderToStaticMarkup(<Dashboard data={trainingStatusFixture} mode="api" />)).not.toContain("Демо данни"));
});
