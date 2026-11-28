// @vitest-environment jsdom
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { planDates } from "../lib/plan-week-view";
import { TrainingPlanSummary } from "../components/training-plan-summary";
import type { DevelopmentSegment, PlanningDraft } from "../lib/training-management";

const loading: DevelopmentSegment = {
  window_start: "2026-11-23", window_end: "2026-11-29", kind: "BUILD", required: true,
  minimum: 2, target: 3, requested: 3, planned: 1, actual: 1, total: 2,
  unknown_actual: 0, unverified_building_labels: 0, assessed: true,
  minimum_shortfall: 0, target_shortfall: 1, status: "TARGET_NOT_MET",
  observed_constraints: ["DAILY_INTENSITY_BUDGET"], hard_limit_proven: false,
};

function plan(segments: DevelopmentSegment[] = []): PlanningDraft {
  return { schema_version: "planning-draft-v1", engine_version: "test", status: "DRAFT",
    start_date: "2026-11-23", end_date: "2026-11-29",
    days: planDates("2026-11-23", "2026-11-29").map(date => ({date, status: "REST", sessions: []})) as unknown as PlanningDraft["days"], warnings: [], source: {},
    parameters: {}, summary: {}, allocation: { components: {}, constraints: [], dose_limits: [],
      development: { version: "test", scope: "WHOLE_MICROCYCLE_ALL_COMPONENTS", segments,
        minimum_shortfall: 0, target_shortfall: 1, needs_search: true, changes_load_limits: false },
    } };
}

const renderedDocument = (value: PlanningDraft) => {
  const result = document.implementation.createHTMLDocument();
  result.body.innerHTML = renderToStaticMarkup(<TrainingPlanSummary plan={value}/>);
  return result;
};
const row = (value: PlanningDraft, component: string) => [...renderedDocument(value).querySelectorAll("tbody tr")]
  .find(element => element.querySelector("th")?.textContent?.startsWith(component))!;

it("keeps the same-period goal, prescription and execution visible while retaining development diagnostics in the payload", () => {
  const value = plan([loading]);
  value.allocation!.components = { Z3: {basis: "DIRECT_Q", target: 120, actual: 20, planned: 999} };
  value.days[0].status = "PLANNED";
  value.days[0].sessions = [{direct_equivalent_minutes: {Z3: 70}}] as unknown as PlanningDraft["days"][number]["sessions"];
  const saved = structuredClone(value);
  const rendered = renderedDocument(value);
  const primary = rendered.querySelector(".q-primary-table")!;
  expect(primary.closest("details")).toBeNull();
  expect(primary.querySelector("caption")?.textContent).toContain("23.11.2026 – 29.11.2026");
  expect([...row(value, "Z3").querySelectorAll("td")].map(cell => cell.textContent)).toEqual([
    "2:00:00100%", "1:10:0058.3% от целта", "0:20:0016.7% от целта",
  ]);
  expect(rendered.body.textContent).not.toContain("Минимум 2; цел 3");
  expect(rendered.body.textContent).not.toContain("DAILY_INTENSITY_BUDGET");
  expect(rendered.body.textContent).not.toContain("TARGET_NOT_MET");
  expect(value).toEqual(saved);
});

it("does not turn unknown execution into zero or expose recovery quota diagnostics", () => {
  const recovery = { ...loading, kind: "RECOVERY", required: false, minimum: 0, target: 0,
    planned: 0, actual: 0, total: 0, unknown_actual: 2, assessed: false,
    target_shortfall: 0, observed_constraints: [], status: "MAINTENANCE_ONLY" as const };
  const value = plan([recovery]);
  value.allocation!.components = {Z3: {basis: "DIRECT_Q", target: 30, actual: null, planned: 0}};
  const saved = structuredClone(value);
  const rendered = renderedDocument(value);
  const actual = row(value, "Z3").querySelectorAll("td")[2];
  expect(actual.textContent).toContain("Няма съпоставима стойност");
  expect(actual.textContent).not.toContain("0:00:00");
  expect(rendered.body.textContent).not.toContain("С неизвестна роля: 2");
  expect(rendered.body.textContent).not.toContain("MAINTENANCE_ONLY");
  expect(value).toEqual(saved);
});

it("retains disabled strength execution without reporting it as a coverage failure", () => {
  const value = plan();
  value.allocation!.components = { STR: { objective_active: false, activity_status: "DISABLED",
    basis: "DIRECT_Q", target: 0, actual: 12, planned: 0, remaining: 0,
    actual_exceeds_target: true, within_tolerance: false, coverage_status: "UNKNOWN" } };
  const rendered = row(value, "Сила");
  expect(rendered.textContent).toContain("Изключено в профила");
  expect(rendered.querySelectorAll("td")[2].textContent).toContain("0:12:00");
  expect(rendered.textContent).not.toContain("%");
  expect(rendered.textContent).not.toContain("Изпълненото е над целта");
  expect(rendered.textContent).not.toContain("UNKNOWN");
});

it("keeps the conflicting manual target and its planned and executed values visible", () => {
  const value = plan();
  value.allocation!.components = { STR: { objective_active: true, activity_status: "CONFLICT",
    basis: "DIRECT_Q", target: 30, actual: 0, planned: 0, remaining: 30,
    within_tolerance: false, coverage_status: "BELOW_RANGE" } };
  const rendered = row(value, "Сила");
  expect(rendered.textContent).toContain("Ръчната цел противоречи на настройките");
  expect([...rendered.querySelectorAll("td")].map(cell => cell.textContent)).toEqual([
    "0:30:00100%", "0:00:000.0% от целта", "0:00:000.0% от целта",
  ]);
  expect(rendered.textContent).not.toContain("BELOW_RANGE");
});
