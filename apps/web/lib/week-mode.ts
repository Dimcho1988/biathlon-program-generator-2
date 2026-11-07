import type { ActivePlanRecord, ManagementProfile, ManagementProfileResponse, PlanningDraft } from "./training-management";
import { isRecord } from "./training-status";

export type WeekMode = ManagementProfile["adaptation_mode"];
export const WEEK_MODE_LABEL: Record<WeekMode, string> = { AUTO: "Адаптивна седмица", REVIEW: "Фиксирана седмица" };

export function proposalHasIncompleteLoad(plan: PlanningDraft | null): boolean {
  const history = plan?.source.planning_history;
  return plan?.activation_eligible !== true && isRecord(history)
    && (history.policy === "UNIFIED_RECORDED_INDICATORS"
      || history.policy === "UNIFIED_RECORDED_INDICATORS_WITH_PLANNING_ESTIMATES") && history.supported === false;
}

function snapshotChoice(plan: PlanningDraft | null): { mode: WeekMode; revision: unknown } | null {
  const snapshot = plan?.input_snapshot;
  if (!isRecord(snapshot) || !isRecord(snapshot.management_profile)) return null;
  const mode = snapshot.management_profile.adaptation_mode;
  return mode === "AUTO" || mode === "REVIEW" ? { mode, revision: snapshot.profile_revision } : null;
}

/** A legacy proposal may carry its mode only in its frozen profile. Never use a newer profile to relabel that proposal. */
export function proposedWeekMode(record: ActivePlanRecord): WeekMode | undefined {
  if (!record.payload.proposal) return undefined;
  return record.payload.proposal_mode ?? snapshotChoice(record.payload.proposal)?.mode;
}

/** Polling may deliver a plan made from a newer saved profile than the initial page props. */
export function savedWeekMode(record: ActivePlanRecord, profile?: ManagementProfileResponse): WeekMode | undefined {
  let mode = profile?.profile?.adaptation_mode;
  let revision = profile?.revision ?? 0;
  for (const plan of [record.payload.plan, record.payload.proposal]) {
    const choice = snapshotChoice(plan);
    if (choice && typeof choice.revision === "number" && Number.isSafeInteger(choice.revision) && choice.revision > revision) {
      mode = choice.mode; revision = choice.revision;
    }
  }
  return mode;
}
