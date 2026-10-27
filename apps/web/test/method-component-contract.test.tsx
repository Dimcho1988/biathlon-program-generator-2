import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { SessionMethodGuidance } from "../components/session-method-guidance";
import { TrainingPlanSummary } from "../components/training-plan-summary";
import { defaultManagementProfile, defaultPlanningControls, parseManagementProfile, type DraftSession, type PlanningDraft } from "../lib/training-management";

const session = {
  method_id: "coach-threshold", title: "Прагови повторения", sport: "Run", zone: "Z3", blocks: [],
  dose_evidence: { effort_anchor: { type: "HR_ZONE_POSITION", zone: "Z3", target_hr_bpm: 170 } },
  q_accounting: { basis: "PLANNED_ESTIMATE_NOT_MEASURED_LOAD", unknown_metabolic_minutes: 0, rows: [
    { kind: "WORK", zone: "Z3", duration_min: 3, coefficient: .8, equivalent_min: 2.4, source: "SPEED" },
    { kind: "WORK", zone: "Z3", duration_min: 3, coefficient: .8, equivalent_min: 2.4, source: "SPEED" },
    { kind: "RECOVERY", zone: "Z1", duration_min: 2, coefficient: .5, equivalent_min: 1, source: "HEART_RATE" },
  ] },
} as unknown as DraftSession;

it("shows the frozen native anchor and an expandable Q calculation without repeating equal work rows", () => {
  const html = renderToStaticMarkup(<SessionMethodGuidance session={session}/>);
  expect(html).toContain("Работна опора:");
  expect(html).toContain("170");
  expect(html).toContain("Как е изчислен Q?");
  expect(html).toContain("0:06:00");
  expect(html).toContain("0:04:48");
  expect(html.match(/<td>Работа<\/td>/g)).toHaveLength(1);
  expect(html).toContain("Прогнозен Q");
  expect(html).toContain("Реалният Q се изчислява от записаното изпълнение");
});

it("retains every combined parent anchor and never renames native work as MAS", () => {
  const combined = structuredClone(session);
  combined.dose_evidence.effort_anchor = { type: "COMBINED", parts: [
    { zone: "Z3", method_id: "threshold", anchor: { type: "HR_ZONE_POSITION", zone: "Z3", target_hr_bpm: 170 } },
    { zone: "Z4", method_id: "source", anchor: { type: "V5", duration_seconds: 300, speed_kmh: 20 } },
  ] };
  const html = renderToStaticMarkup(<SessionMethodGuidance session={combined}/>);
  expect(html).toContain("Индивидуална зонова опора");
  expect(html).toContain("V(5 мин)");
  expect(html).not.toContain("MAS");
});

it("does not clamp an overshoot to 100% or transfer it to an underfilled component", () => {
  const plan = { start_date: "2026-10-26", end_date: "2026-11-01", days: [], parameters: {}, summary: {}, allocation: {
    components: {
      Z3: { basis: "DIRECT_Q", target: 100, actual: 0, planned: 120, remaining: 0, coverage_status: "ABOVE_RANGE", within_tolerance: false },
      Z4: { basis: "DIRECT_Q", target: 100, actual: 0, planned: 80, remaining: 20, coverage_status: "BELOW_RANGE", within_tolerance: false },
    }, constraints: [], dose_limits: [], acceptance: { lower_percent: 90, upper_percent: 110, target_percent: 100, within_tolerance: false },
  } } as unknown as PlanningDraft;
  const html = renderToStaticMarkup(<TrainingPlanSummary plan={plan}/>);
  expect(html).toContain("120.0%");
  expect(html).toContain("80.0%");
  expect(html).toContain("Над 110%");
  expect(html).toContain("Под 90%");
  expect(html).toContain("Целим 100% за всеки компонент във всеки микроцикъл");
  expect(html).toContain("Компоненти извън допустимите граници");
  for (const row of Object.values(plan.allocation!.components)) {
    row!.planned = 95;
    row!.remaining = 5;
    row!.within_tolerance = true;
    row!.coverage_status = "WITHIN_RANGE";
  }
  const accepted = renderToStaticMarkup(<TrainingPlanSummary plan={plan}/>);
  expect(accepted).toContain("95.0%");
  expect(accepted).not.toContain("Остава непланиран товар");
  expect(accepted).not.toContain("Компоненти извън допустимите граници");
});

it("accepts automatic strength frequency while retaining saved explicit limits", () => {
  const profile = defaultManagementProfile("2026-10-26");
  profile.discipline = "5000 m";
  profile.planning_controls = defaultPlanningControls("Run");
  expect(profile.planning_controls.max_strength_sessions).toBeNull();
  expect(parseManagementProfile(profile).planning_controls?.max_strength_sessions).toBeNull();
  profile.planning_controls.max_strength_sessions = 2;
  expect(parseManagementProfile(profile).planning_controls?.max_strength_sessions).toBe(2);
  profile.planning_controls.max_strength_sessions = 4;
  expect(parseManagementProfile(profile).planning_controls?.max_strength_sessions).toBe(4);
});
