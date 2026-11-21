import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
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
    start_date: "2026-11-23", end_date: "2026-11-29", days: [], warnings: [], source: {},
    parameters: {}, summary: {}, allocation: { components: {}, constraints: [], dose_limits: [],
      development: { version: "test", scope: "WHOLE_MICROCYCLE_ALL_COMPONENTS", segments,
        minimum_shortfall: 0, target_shortfall: 1, needs_search: true, changes_load_limits: false },
    } };
}

const text = (value: PlanningDraft) => renderToStaticMarkup(<TrainingPlanSummary plan={value}/>).replace(/<[^>]+>/g, "");

it("distinguishes the loading minimum from the desired count using actual plus forecast", () => {
  const rendered = text(plan([loading]));
  expect(rendered).toContain("1 + 1 = 2");
  expect(rendered).toContain("Минимум 2; цел 3");
  expect(rendered).toContain("Минимумът е покрит; желаният брой още не е достигнат");
  expect(rendered).not.toContain("Желаният брой е покрит");
  expect(rendered).toContain("Недостигът не доказва");
  expect(rendered).toContain("Сред отчетените ограничения: дневен бюджет за интензивна работа");
});

it("does not invent building requirements in recovery or count unknown execution roles", () => {
  const recovery = { ...loading, kind: "RECOVERY", required: false, minimum: 0, target: 0,
    planned: 0, actual: 0, total: 0, unknown_actual: 2, assessed: false,
    target_shortfall: 0, observed_constraints: [], status: "MAINTENANCE_ONLY" as const };
  const rendered = text(plan([recovery]));
  expect(rendered).toContain("Поддържаща работа");
  expect(rendered).toContain("Няма изискване за изграждащи занимания");
  expect(rendered).toContain("С неизвестна роля: 2 изпълнени занимания");
  expect(rendered).not.toContain("Минимум 2");
});

it("retains disabled strength execution without reporting it as a coverage failure", () => {
  const value = plan();
  value.allocation!.components = { STR: { objective_active: false, activity_status: "DISABLED",
    basis: "DIRECT_Q", target: 0, actual: 12, planned: 0, remaining: 0,
    actual_exceeds_target: true, within_tolerance: false, coverage_status: "UNKNOWN" } };
  const rendered = text(value);
  expect(rendered).toContain("Изключено в профила");
  expect(rendered).toContain("0:12:00");
  expect(rendered).toContain("Не участва в покритието");
  expect(rendered).not.toContain("Изпълненото е над целта");
  expect(rendered).not.toContain("Компоненти извън допустимите граници");
});

it("keeps a conflicting manual target visible and assessed", () => {
  const value = plan();
  value.allocation!.components = { STR: { objective_active: true, activity_status: "CONFLICT",
    basis: "DIRECT_Q", target: 30, actual: 0, planned: 0, remaining: 30,
    within_tolerance: false, coverage_status: "BELOW_RANGE" } };
  const rendered = text(value);
  expect(rendered).toContain("Ръчната цел противоречи на настройките");
  expect(rendered).toContain("0:30:00");
  expect(rendered).toContain("Компоненти извън допустимите граници: STR");
  expect(rendered).toContain("0.0%");
});
