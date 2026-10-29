import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { SpeedModelPanel } from "../components/speed-model";
import { BlockExecutionRange } from "../components/block-execution-range";
import { MethodCard } from "../components/training-method-library";
import { SessionMethodGuidance } from "../components/session-method-guidance";
import { parseSpeedModel, type SpeedModel } from "../lib/models";
import type { LibraryMethod } from "../lib/method-library";
import type { DraftSession, SessionBlock } from "../lib/training-management";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));

const source = "INITIAL_HEURISTIC_V50_REFERENCE_SCALE";
const model: SpeedModel = {
  schema_version: "speed-model-v1", model_version: "heuristic_v0.1_unvalidated", status: "INITIAL_HEURISTIC",
  sport: "Run", sports: ["Run"], activities: [], tests: [], active_test_count: 0, active_test_keys: [],
  speed_role: "RAW_MOVING_SPEED", curve_metadata: { mode: "HEURISTIC_REFERENCE_SCALE", absolute_speed_available: true },
  initial_speed_profile: { source, zone_policy_version: "initial-heuristic-v50-reference-scale-v1", is_estimated: true, calibration_eligible: false },
  initial_threshold: { model_version: "heuristic_v0.1_unvalidated", status: "AVAILABLE", use_status: "ZONES", reason: null,
    p_percent: 80.5476, v_threshold_estimated_kmh: 14.898, speed_role: "RAW_MOVING_SPEED", is_estimated: true,
    calibration_eligible: false, used_for_zones: true, inputs: { H: 8, v: 12, S: 4 }, window: { start: "2026-09-30", end_exclusive: "2026-10-28", days: 28 } },
  points: [ { duration_s: 180, speed_kmh: 20, distance_m: 1000, estimated_hr_bpm: 180, evidence: "INITIAL_HEURISTIC_ESTIMATE" },
    { duration_s: 3000, speed_kmh: 14.898, distance_m: 12415, estimated_hr_bpm: 165, evidence: "INITIAL_HEURISTIC_ESTIMATE" } ],
  hr_model: { model_version: "initial", speed_role: "RAW_MOVING_SPEED", hr_range_bpm: [120, 180], speed_range_kmh: [8, 20],
    zones: [{ zone: "Z3", hr_bpm: 165, duration_s: 3000, duration_min_s: 2700, duration_max_s: 3600, speed_kmh: 14.898, source, reason: null, index: null, count: 0 }] },
  speed_zones: { status: "AVAILABLE", speed_role: "RAW_MOVING_SPEED", zones: Array.from({ length: 5 }, (_, i) => ({ zone: `Z${i+1}`, low_kmh: i*4, high_kmh: i < 4 ? (i+1)*4 : null, source })) },
  volume_weekly_min: {}, history_days: 28, zone_corrections: {}, correction_applied_fraction: 0,
  critical_speed: { status: "INSUFFICIENT_TESTS", count: 0, standardized: { status: "UNAVAILABLE", points: [] } },
  prediction: { duration_s: 3000, speed_kmh: 14.898, distance_m: 12415, estimated_hr_bpm: 165, evidence: "INITIAL_HEURISTIC_ESTIMATE" },
  warnings: [], source_generation_id: "test", source_revision: 1, hr_speed_range_kmh: [8, 20],
};

it("accepts active estimated zones but rejects contradictory calibration or speed provenance", () => {
  expect(parseSpeedModel(model).initial_threshold?.used_for_zones).toBe(true);
  for (const patch of [{ speed_role: "FLAT_EQUIVALENT" }, { initial_speed_profile: { ...model.initial_speed_profile, calibration_eligible: true } },
    { initial_threshold: { ...model.initial_threshold, used_for_zones: false } }]) {
    expect(() => parseSpeedModel({ ...model, ...patch })).toThrow();
  }
});

it("renders usable initial forecasts and five zones with raw-speed provenance and no real-test claims", () => {
  const html = renderToStaticMarkup(<SpeedModelPanel model={model} canEdit={false}/>);
  for (const text of ["Начална моделна крива", "временна V50 опора", "без корекция за релефа", "км/ч · скорост в движение", "Начална оценка · V50 и нормативна форма", "Дистанция · без корекция за релефа", "Цялата прекъсната линия е начална моделна оценка"]) expect(html).toContain(text);
  expect(html).not.toMatch(/disabled=""[^>]*>Изчисли/);
  expect(html).not.toContain("CS по 3 и 12 мин");
  expect(html).not.toContain("Твоят скоростно-издръжливостен профил");
  expect(html).not.toContain("Използвано време / Vflat");
  expect(html).not.toContain("скоростните зони изискват допълнителни индивидуални опори");
  expect(html).toContain('stroke-dasharray="6 4"');
});

it("keeps the usable initial estimate when conflicting tests need review", () => {
  const html = renderToStaticMarkup(<SpeedModelPanel model={{ ...model, warnings: ["CONFLICTING_TESTS"] }} canEdit={false}/>);
  expect(html).toContain("докато тестовете бъдат прегледани");
  expect(html).not.toContain("Индивидуалните прогнози са спрени");
});

it("keeps a valid individual index usable while conflicting tests need review", () => {
  const html = renderToStaticMarkup(<SpeedModelPanel model={{ ...model, status: "PRELIMINARY", speed_role: "FLAT_EQUIVALENT",
    initial_threshold: { ...model.initial_threshold!, use_status: "SUPERSEDED", used_for_zones: false, superseded_by: "INDEX" },
    warnings: ["CONFLICTING_TESTS"] }} canEdit={false}/>);
  expect(html).toContain("Използва се наличният валиден индивидуален индекс");
  expect(html).not.toContain("Индивидуалните прогнози са спрени");
  expect(html).not.toMatch(/disabled=""[^>]*>Изчисли/);
});

const block: SessionBlock = { kind: "WORK", label: "Работа", zone: "Z2", duration_min: 10, target_hr_bpm: null,
  target_speed_kmh: 12, repetition: null, instructions: "Равномерно усилие.", primary_control: "EFFORT_ON_TERRAIN",
  speed_basis: "RAW_MOVING_SPEED", distance_basis: "RAW_MOVING_SPEED", target_speed_range_kmh: [11, 12], distance_range_m: [1800, 2000] };
const method: LibraryMethod = { id: "R-INITIAL", title: "Начален ориентир", sports: ["Run"], periods: [], disciplines: [], source: "Треньорска адаптация", guidance: {},
  resolution: { status: "READY", anchor: { type: "V50", speed_kmh: 14.898 }, target_speed_range_kmh: [11, 12], speed_role: "RAW_MOVING_SPEED", curve_source: "INITIAL_HEURISTIC" } };

it("labels method and saved-session targets as raw even when terrain-effort control is present", () => {
  const html = renderToStaticMarkup(<><BlockExecutionRange block={block} sport="Run"/><MethodCard method={method} canEdit={false} selected onChange={() => {}} onReset={() => {}}/>
    <SessionMethodGuidance session={{ sport: "Run", blocks: [block], method_library: method,
      dose_evidence: { effort_anchor: { type: "V50", speed_kmh: 14.898, speed_role: "RAW_MOVING_SPEED" } } } as unknown as DraftSession}/></>);
  expect(html).toContain("Скорост в движение · начална оценка");
  expect(html).toContain("Без корекция за релефа");
  expect(html).toContain("прогнозна V50 и нормативна форма");
  expect(html).not.toContain("Равнинен еквивалент");
  expect(html).not.toContain("Равнинна референция");
  expect(html).not.toContain("Еквивалентна дистанция");
});
