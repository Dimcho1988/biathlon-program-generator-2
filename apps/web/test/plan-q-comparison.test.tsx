import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { PlanQComparison } from "../components/plan-q-comparison";
import { planQComparison } from "../lib/plan-q-comparison";
import { parseTrainingStatus, type LoadSelectionMetadata } from "../lib/training-status";
import { trainingStatusFixture } from "../lib/fixture";
import type { PlanningDraft } from "../lib/training-management";

const plan = () => ({ schema_version: "planning-draft-v1", status: "DRAFT", start_date: "2026-10-25", end_date: "2026-10-31",
  generated_at: "2026-10-25T14:00:00Z", days: [{date: "2026-10-25", status: "PLANNED", sessions: [{direct_equivalent_minutes: {Z1: 80, Z3: 10, Z4: 0}}]}],
  lookahead_days: [{date: "2026-11-01", status: "PLANNED", sessions: [{direct_equivalent_minutes: {Z1: 120}}]}], allocation: { scope: "WHOLE_MICROCYCLES", constraints: [], dose_limits: [],
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
  expect(draft).toContain("Планирано · предложение"); expect(draft).not.toContain("Планирано · утвърдено");
  expect(draft).toContain("2:00:00"); expect(draft).toContain("1:20:00"); expect(draft).toContain("0:30:00");
  expect(draft).toContain("Няма съпоставима стойност"); expect(draft).toContain("66.7% от целта");
  expect(draft).toContain("24.10.2026 – 30.10.2026"); expect(draft).not.toContain("16:39:00");
  const approved = renderToStaticMarkup(<PlanQComparison plan={plan()} planKind="APPROVED"/>);
  expect(approved).toContain("Планирано · утвърдено"); expect(approved).not.toContain("Планирано · предложение");
  expect(approved).toContain("Архивна оценка на изпълнението при съставяне на плана");
});

it("does not require allocation for historical plans and retains explicitly direct legacy units", () => {
  const p = plan(); delete p.allocation;
  const incomplete = planQComparison(p)[0];
  expect(incomplete.rows[0]).toMatchObject({target: null, actual: null, planned: 80});
  expect(renderToStaticMarkup(<PlanQComparison plan={p}/>)).toContain("Целта по Q е неизвестна");
  p.allocation = {constraints: [], dose_limits: [], components: {Z1: {basis: "DIRECT_Q", target: 10, planned: 4, actual: 2}, Z2: {target: 50, planned: 20, actual: 10}}};
  const rows = planQComparison(p)[0].rows;
  expect(rows[0]).toMatchObject({target: 10, planned: 80, actual: 2});
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

it("refreshes actual Q without adding it to the approved prescription or forecast", () => {
  const p = plan();
  const saved = structuredClone(p);
  const execution = {schema_version: "direct-q-execution-v1" as const, as_of: "2026-10-26", days: [
    {date: "2026-10-24", q: {Z1: 0}, day_complete: true, activity_count: 0},
    {date: "2026-10-25", q: {Z1: 20}, day_complete: true, activity_count: 2},
    {date: "2026-10-26", q: {Z1: 5}, day_complete: false, activity_count: 1}]};
  const periods = planQComparison(p, {planKind: "APPROVED", qExecution: execution});
  expect(periods[0].rows[0]).toMatchObject({target: 120, planned: 80, actual: 25, forecast: 0,
    plannedCompleted: 80, actualCompleted: 20});
  expect(periods[1].rows[0]).toMatchObject({target: 130, planned: 0, actual: 0, forecast: 120});
  expect(p).toEqual(saved);
  execution.days[1].q.Z1 = 30;
  expect(planQComparison(p, {qExecution: execution})[0].rows[0].actual).toBe(35);
});

it("missing dates or Q do not disappear from execution or adherence totals", () => {
  const execution = {schema_version: "direct-q-execution-v1" as const, as_of: "2026-10-26", days: []};
  const r = planQComparison(plan(), {planKind: "APPROVED", qExecution: execution})[0].rows[0];
  expect(r.actual).toBeNull(); expect(r.actualCompleted).toBeNull(); expect(r.plannedCompleted).toBe(80);
});

it("an adapted approved plan retains historical prescriptions while a proposal reports only its own tasks", () => {
  const p = plan(); p.start_date = "2026-10-26"; p.days = [];
  const outcomes = [{date: "2026-10-25", planned_q: {Z1: 80}, planned_minutes: 100, actual_minutes: 90,
    planned_title: "Prior prescription", status: "RECORDED"}];
  expect(planQComparison(p, {planKind: "APPROVED", outcomes})[0].rows[0].planned).toBe(80);
  expect(planQComparison(p, {planKind: "DRAFT", outcomes})[0].rows[0].planned).toBe(0);
  delete (outcomes[0] as {planned_q?: unknown}).planned_q;
  expect(planQComparison(p, {planKind: "APPROVED", outcomes})[0].rows[0].planned).toBeNull();
});

it("defaults to the current microcycle and keeps provisional Q outside the main three columns", () => {
  const markup = renderToStaticMarkup(<PlanQComparison plan={plan()} today="2026-11-01"/>);
  expect(markup).toContain('<option value="2026-10-31" selected="">');
  expect(markup).toContain("Прогнозно продължение извън показания график");
  expect(markup).toContain("ниска стойност в незавършен цикъл сама по себе си не означава изоставане");
});
