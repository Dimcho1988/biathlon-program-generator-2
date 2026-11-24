import Link from "next/link";
import { isRecord } from "../lib/training-status";
import type { PlanningDraft } from "../lib/training-management";
import { PlanQComparison } from "./plan-q-comparison";
import type { QComparisonOptions } from "../lib/plan-q-comparison";

/** The detailed algorithm evidence remains in the plan payload and exports. */
export function TrainingPlanSummary({plan, planKind = "DRAFT", ...reportOptions}: {plan: PlanningDraft} & QComparisonOptions) {
  const snapshot = isRecord(plan.input_snapshot) ? plan.input_snapshot : {};
  const profile = isRecord(snapshot.management_profile) ? snapshot.management_profile : {};
  const manual = isRecord(profile.component_targets_weekly) && Object.values(profile.component_targets_weekly).some(value => typeof value === "number");
  return <section className="management-panel">
    <PlanQComparison plan={plan} planKind={planKind} {...reportOptions}/>
    {manual && <p className="management-muted">Използват се ръчни цели. <Link href="/planning">Прегледай целите в профила →</Link></p>}
  </section>;
}
