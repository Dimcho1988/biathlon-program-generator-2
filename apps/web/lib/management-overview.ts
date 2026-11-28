import { isCalendarDate, isRecord } from "./training-status";
import { parseManagementProfileResponse, parseManagementOutlook, parseDrafts, parseActivePlanResponse,
  type ActivePlanRecord, type ActivePlanResponse, type PlanComparisonOutcome, type SessionVolumePlan } from "./training-management";
import { parseSyncState } from "./sync";

export interface ManagementOverviewActive extends SessionVolumePlan {
  revision: number | null;
  stale: boolean;
  status: ActivePlanRecord["payload"]["status"] | null;
  has_proposal: boolean;
  engine_version: string | null;
  history_comparison: unknown[];
  outcomes: PlanComparisonOutcome[];
}

export interface ManagementPreparation {
  status: "READY" | "UPDATING";
  prepared_at: string | null;
  evaluated_on: string | null;
  reason: string | null;
}

function parsePreparation(value: unknown): ManagementPreparation | null {
  if (!isRecord(value) || (value.status !== "READY" && value.status !== "UPDATING")) return null;
  const prepared_at = typeof value.prepared_at === "string" && Number.isFinite(Date.parse(value.prepared_at))
    ? value.prepared_at : null;
  const evaluated_on = isCalendarDate(value.evaluated_on) ? value.evaluated_on : null;
  if (value.status === "READY" && (!prepared_at || !evaluated_on)) return null;
  return {status: value.status, prepared_at, evaluated_on,
    reason: value.status === "READY" ? null : typeof value.reason === "string" ? value.reason : "PREPARATION_PENDING"};
}

const nonnegative = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value) && value >= 0;
const nullableMinutes = (value: unknown) => value === null || nonnegative(value);
const invalidOverview = () => new Error("Невалиден преглед на тренировъчната програма.");

export function parseManagementOverviewActive(value: unknown): ManagementOverviewActive {
  if (!isRecord(value)
    || !(value.revision === null || (Number.isSafeInteger(value.revision) && Number(value.revision) > 0))
    || typeof value.stale !== "boolean" || typeof value.has_proposal !== "boolean"
    || !(value.status === null || ["ACTIVE", "PAUSED", "REVIEW_REQUIRED", "COMPLETED"].includes(String(value.status)))
    || !(value.engine_version === null || typeof value.engine_version === "string")
    || !Array.isArray(value.days) || !Array.isArray(value.history_comparison) || !Array.isArray(value.outcomes)) throw invalidOverview();
  for (const day of value.days) {
    if (!isRecord(day) || !isCalendarDate(day.date) || typeof day.status !== "string" || !Array.isArray(day.sessions)) throw invalidOverview();
    for (const session of day.sessions) {
      if (!isRecord(session) || !nonnegative(session.total_minutes) || !Array.isArray(session.blocks)) throw invalidOverview();
      for (const block of session.blocks) if (!isRecord(block) || typeof block.zone !== "string" || !nonnegative(block.duration_min)) throw invalidOverview();
    }
  }
  for (const outcome of value.outcomes) if (!isRecord(outcome) || !isCalendarDate(outcome.date)
    || !nullableMinutes(outcome.planned_minutes) || !nullableMinutes(outcome.actual_minutes)) throw invalidOverview();
  // A missing active revision cannot supply prescribed time or historical outcomes.
  if (value.revision === null && (value.status !== null || value.engine_version !== null || value.stale || value.has_proposal
    || value.days.length || value.history_comparison.length || value.outcomes.length)) throw invalidOverview();
  if (value.revision !== null && (value.status === null || value.engine_version === null)) throw invalidOverview();
  return value as unknown as ManagementOverviewActive;
}

/** Older API versions ignore the opt-in projection and retain the full response. */
export function parseManagementView(value: unknown) {
  if (!isRecord(value)) throw new Error("Управлението временно не е достъпно.");
  if (value.schema_version !== undefined && value.schema_version !== "management-overview-v1") throw invalidOverview();
  const overviewActive = value.schema_version === "management-overview-v1"
    ? parseManagementOverviewActive(value.overview_active) : undefined;
  const emptyActive: ActivePlanResponse = parseActivePlanResponse({active: null, history: [],
    ...(value.capabilities === undefined ? {} : {capabilities: value.capabilities})});
  const sync = value.sync == null ? null : parseSyncState(value.sync);
  return {
    profile: parseManagementProfileResponse(value.profile),
    active: overviewActive ? emptyActive : parseActivePlanResponse(value.active),
    overviewActive,
    drafts: parseDrafts(value.drafts),
    outlook: value.outlook == null ? null : parseManagementOutlook(value.outlook),
    sync,
    syncUnavailable: value.sync_unavailable === true,
    renderedGenerationId: value.rendered_generation_id === null || typeof value.rendered_generation_id === "string"
      ? value.rendered_generation_id : sync?.active_generation_id ?? null,
    preparation: parsePreparation(value.preparation),
    inputFingerprint: isRecord(value.drafts) && typeof value.drafts.current_input_fingerprint === "string"
      ? value.drafts.current_input_fingerprint : "",
  };
}
