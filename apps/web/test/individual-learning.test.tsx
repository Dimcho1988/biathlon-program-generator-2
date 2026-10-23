import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { IndividualLearningPanel } from "../components/individual-learning-panel";
import { defaultIndividualLearning, parseIndividualLearningConfig, parseIndividualLearningReport, type IndividualLearningReport } from "../lib/individual-learning";
import { COMPONENTS, defaultManagementProfile, parseManagementProfile } from "../lib/training-management";

const report: IndividualLearningReport = {
  version: "individual-learning-v1", mode: "SHADOW", status: "OBSERVING",
  as_of: "2026-09-27", effective_from: "2026-09-28", expires_on: "2026-10-04",
  summary: "Предлага се малка промяна за Z2; наблюдението продължава.",
  evidence_count: 18, confidence: "MEDIUM",
  validation: { status: "PASSED", evaluated: 7, model_mae: 1.2, baseline_mae: 1.8 },
  components: Object.fromEntries(COMPONENTS.map(key => [key, {
    volume_factor: 1, proposed_volume_factor: key === "Z2" ? 1.05 : 1,
    intensity_delta: 0, proposed_intensity_delta: key === "Z2" ? .02 : 0,
    action: key === "Z2" ? "INCREASE" : "HOLD", evidence_count: 8, confidence: "MEDIUM",
    reason: key === "Z2" ? "Съпоставимите наблюдения подкрепят пробна промяна." : "Запазваме текущата цел.",
  }])) as IndividualLearningReport["components"], limitations: ["Все още липсват достатъчно потвърдени методи."],
};

describe("individual learning presentation", () => {
  it("keeps old profiles in observation mode without replacing their training settings", () => {
    const legacy = { ...defaultManagementProfile("2026-09-27"), discipline: "1500 m", individual_learning: undefined,
      available_minutes: [75, 90, 0, 60, 100, 120, 80], building_fraction: .6 };
    const parsed = parseManagementProfile(legacy);
    expect(parsed.individual_learning).toEqual(defaultIndividualLearning());
    expect(parsed.available_minutes).toEqual(legacy.available_minutes);
    expect(parsed.building_fraction).toBe(.6);
    expect(parseManagementProfile({ ...legacy, individual_learning: { ...defaultIndividualLearning(), mode: "OFF" } }).individual_learning?.mode).toBe("OFF");
  });
  it.each([{ mode: "AUTO" }, { max_volume_step_percent: 11 }, { max_intensity_step: .06 }, { exploration_enabled: "yes" }, { max_volume_step_percent: NaN }])("rejects invalid learning configuration %s", patch => {
    expect(() => parseIndividualLearningConfig({ ...defaultIndividualLearning(), ...patch })).toThrow();
  });
  it("handles old saved plans and malformed reports without breaking the programme", () => {
    expect(parseIndividualLearningReport(undefined)).toBeNull();
    expect(renderToStaticMarkup(<IndividualLearningPanel plan={{ parameters: {} }}/>)).toBe("");
    expect(renderToStaticMarkup(<IndividualLearningPanel plan={{ parameters: { individual_learning: { ...report, confidence: "certain" } } }}/>))
      .toContain("Обнови програмата");
    expect(() => parseIndividualLearningReport({ ...report, components: { Z2: report.components.Z2 } })).toThrow();
    expect(() => parseIndividualLearningReport({ ...report, as_of: "2026-02-30" })).toThrow();
  });
  it("separates shadow proposals from the unchanged prescription, without exposing internal memory", () => {
    const html = renderToStaticMarkup(<IndividualLearningPanel plan={{ parameters: {
      individual_learning: { ...report, memory: { private_feature: "INTERNAL_MEMORY" } },
    } }}/>);
    expect(html).toContain("Наблюдение");
    expect(html).toContain("18 наблюдения");
    expect(html).toContain("Предложено");
    expect(html).toContain("Допуснато в плана");
    expect(html).toContain("обем +5% · в зоната +2 п.п.");
    expect(html).toContain("обем Без промяна");
    expect(html).toContain("не променя предписаното натоварване");
    expect(html).toContain("7 проверки върху последващи данни");
    expect(html).not.toContain("INTERNAL_MEMORY");
    expect(html).not.toContain(' open=""');
    expect(html).not.toContain("NaN");
  });
  it("reports applied corrections and prediction warmup without claiming scientific proof", () => {
    const value = { ...report, mode: "CONTROL", validation: { ...report.validation, status: "WARMUP", evaluated: 0 } };
    const html = renderToStaticMarkup(<IndividualLearningPanel plan={{ parameters: { individual_learning: value } }}/>);
    expect(html).toContain("Управление");
    expect(html).toContain("Натрупват се данни за проверка");
    expect(html).toContain("не доказва");
    expect(html).not.toContain("Предложенията са за наблюдение");
  });
  it("reads the outlook report at its actual API location and does not equate policy with a final prescription", () => {
    const html = renderToStaticMarkup(<IndividualLearningPanel plan={{ individual_learning: { ...report, mode: "CONTROL" } }}/>);
    expect(html).toContain("Самообучение");
    expect(html).toContain("Действителната промяна може да е по-малка или нулева");
  });
  it("distinguishes examined history from usable current evidence and explains activation", () => {
    const value = { ...report, examined_period_count: 5, archived_evidence_count: 3, evidence_count: 2, out_of_support_count: 1 };
    const html = renderToStaticMarkup(<IndividualLearningPanel plan={{ individual_learning: value }}/>);
    expect(html).toContain("5 разгледани периода");
    expect(html).toContain("3 с достатъчни наблюдения");
    expect(html).toContain("2 съпоставими");
    expect(html).toContain("5% е началната граница");
    expect(html).toContain("Обнови сега");
    expect(() => parseIndividualLearningReport({ ...value, examined_period_count: -1 })).toThrow();
  });
});
