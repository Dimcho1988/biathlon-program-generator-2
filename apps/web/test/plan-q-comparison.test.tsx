import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { PlanQComparison } from "../components/plan-q-comparison";
import { planQComparison } from "../lib/plan-q-comparison";
import { parseTrainingStatus, type LoadSelectionMetadata } from "../lib/training-status";
import { trainingStatusFixture } from "../lib/fixture";
import type { PlanningDraft } from "../lib/training-management";

const plan = () => ({ schema_version: "planning-draft-v1", status: "DRAFT", start_date: "2026-10-25", end_date: "2026-10-31",
  generated_at: "2026-10-25T14:00:00Z", days: [], allocation: { scope: "WHOLE_MICROCYCLES", constraints: [], dose_limits: [],
    components: {Z1: {basis: "DIRECT_Q", target_q: 999, actual_q: 999, planned_q: 999}}, segments: [
      {window_start: "2026-10-24", window_end: "2026-10-30", days: 7, components: {
        Z1: {basis: "DIRECT_Q", target_q: 120, actual_q: 30, planned_q: 80},
        Z3: {basis: "CANONICAL_E", target: 50, target_q: null, actual: 20, actual_q: null, planned_q: 10},
        Z4: {basis: "DIRECT_Q", target_q: 0, actual_q: 0, planned_q: 0}}},
      {window_start: "2026-10-31", window_end: "2026-11-06", days: 7, components: {
        Z1: {basis: "DIRECT_Q", target_q: 130, actual_q: 0, planned_q: 120}}}]},
}) as unknown as PlanningDraft;

it("uses exact microcycle windows, preserves genuine zeros and never substitutes E for missing Q", () => {
  const input = plan();
  const saved = structuredClone(input);
  const periods = planQComparison(input);
  expect(periods.map(p => [p.start, p.end, p.includesForecast])).toEqual([
    ["2026-10-24", "2026-10-30", false], ["2026-10-31", "2026-11-06", true]]);
  expect(periods[0].rows[0]).toMatchObject({zone: "Z1", target: 120, actual: 30, planned: 80});
  expect(periods[0].rows[2]).toMatchObject({zone: "Z3", target: null, actual: null, planned: 10});
  expect(periods[0].rows[3]).toMatchObject({zone: "Z4", target: 0, actual: 0, planned: 0});
  expect(periods[0].rows[4].target).toBeNull();
  expect(input).toEqual(saved);
});

it("keeps approved and proposed labels separate and publishes accessible exact values", () => {
  const draft = renderToStaticMarkup(<PlanQComparison plan={plan()}/>);
  expect(draft).toContain("Предложено Q"); expect(draft).not.toContain("Утвърдено Q");
  expect(draft).toContain("2:00:00"); expect(draft).toContain("1:20:00"); expect(draft).toContain("0:30:00");
  expect(draft).toContain("Няма съпоставима стойност"); expect(draft).toContain('role="img"');
  expect(draft).toContain("24.10.2026 – 30.10.2026"); expect(draft).not.toContain("16:39:00");
  const approved = renderToStaticMarkup(<PlanQComparison plan={plan()} planKind="APPROVED"/>);
  expect(approved).toContain("Утвърдено Q"); expect(approved).not.toContain("Предложено Q");
  expect(approved).toContain("Стойностите са от входните данни при съставянето на тази версия");
});

it("does not require allocation for historical plans and retains explicitly direct legacy units", () => {
  const p = plan(); delete p.allocation;
  const incomplete = planQComparison(p)[0];
  expect(incomplete.rows[0]).toMatchObject({target: null, actual: null, planned: 0});
  expect(renderToStaticMarkup(<PlanQComparison plan={p}/>)).toContain("няма завършен компонентен разчет");
  p.allocation = {constraints: [], dose_limits: [], components: {Z1: {basis: "DIRECT_Q", target: 10, planned: 4, actual: 2}, Z2: {target: 50, planned: 20, actual: 10}}};
  const rows = planQComparison(p)[0].rows;
  expect(rows[0]).toMatchObject({target: 10, planned: 4, actual: 2});
  expect(rows[1]).toMatchObject({target: null, planned: null, actual: null});
});

const metadata = (): LoadSelectionMetadata => ({schema_version: "load-source-metadata-v1", scope: "RECORDED_AEROBIC_ACTIVITIES_IN_PERIOD",
  leading_indicator: "HEART_RATE", ledger_versions: ["v4"], hr_coordinate_policies: [], activity_count: 1, total_aerobic_activities: 2,
  recorded_seconds: 60, active_seconds: 40, rest_seconds: 10, unknown_seconds: 10, fallback_seconds: 5,
  source_seconds: {HEART_RATE: 45, SPEED: 5}, hr_source_seconds: {HRMOD: 40, RAW_HR: 5}, speed_status_seconds: null,
  speed_unavailable_reasons_seconds: null, unavailable_speed_hr_seconds: null, estimated_descent_seconds: null});

it("accepts old API models and validates new coverage instead of accepting misleading source totals", () => {
  expect(parseTrainingStatus(trainingStatusFixture)).toEqual(trainingStatusFixture);
  const value = {...trainingStatusFixture, model: {...trainingStatusFixture.model, load_selection: metadata()}};
  expect(parseTrainingStatus(value).model.load_selection?.source_seconds.SPEED).toBe(5);
  for (const broken of [{...metadata(), recorded_seconds: 90}, {...metadata(), rest_seconds: -1},
    {...metadata(), source_seconds: {HEART_RATE: 60, SPEED: 5}}, {...metadata(), activity_count: 3},
    {...metadata(), hr_source_seconds: {HRMOD: 60}}, {...metadata(), unavailable_speed_hr_seconds: 50}])
    expect(() => parseTrainingStatus({...value, model: {...value.model, load_selection: broken}})).toThrow(/метаданни/);
});
