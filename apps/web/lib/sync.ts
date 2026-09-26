import { isCalendarDate, isRecord } from "./training-status";

export const SYNC_SCOPES = ["FULL", "WELLNESS", "RECOVERY"] as const;
export const SYNC_STATES = [
  "IDLE",
  "QUEUED",
  "RUNNING",
  "RETRY_WAIT",
  "SUCCEEDED",
  "FAILED",
  "SUPERSEDED",
] as const;

export type SyncScope = (typeof SYNC_SCOPES)[number];
export type SyncStateName = (typeof SYNC_STATES)[number];

export interface SyncEnqueueResponse {
  schema_version: "sync-enqueue-v1";
  job_id: string;
  scope: SyncScope;
  state: "QUEUED" | "RUNNING";
  coalesced: boolean;
}

export interface SyncState {
  schema_version: "sync-state-v1";
  job_id: string | null;
  scope: SyncScope | null;
  state: SyncStateName;
  stage: string | null;
  progress_percent: number;
  requested_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  retry_at: string | null;
  failure_code: string | null;
  active_generation_id: string | null;
  active_revision: number;
  analysis_as_of: string | null;
  activated_at: string | null;
}

const enqueueKeys = ["schema_version", "job_id", "scope", "state", "coalesced"];
const stateKeys = [
  "schema_version",
  "job_id",
  "scope",
  "state",
  "stage",
  "progress_percent",
  "requested_at",
  "started_at",
  "finished_at",
  "retry_at",
  "failure_code",
  "active_generation_id",
  "active_revision",
  "analysis_as_of",
  "activated_at",
];
const scopeSet = new Set<string>(SYNC_SCOPES);
const stateSet = new Set<string>(SYNC_STATES);
const exactKeys = (value: Record<string, unknown>, keys: string[]) =>
  Object.keys(value).length === keys.length && keys.every((key) => key in value);
const nullableString = (value: unknown): value is string | null =>
  value === null || typeof value === "string";
const nullableTimestamp = (value: unknown): value is string | null =>
  value === null || (
    typeof value === "string" &&
    value.length <= 64 &&
    Number.isFinite(Date.parse(value))
  );
const identifier = (value: unknown): value is string =>
  typeof value === "string" && value.length > 0 && value.length <= 128;

export function parseSyncEnqueueResponse(value: unknown): SyncEnqueueResponse {
  if (!isRecord(value) || !exactKeys(value, enqueueKeys))
    throw new Error("API услугата върна невалиден sync enqueue отговор.");
  if (
    value.schema_version !== "sync-enqueue-v1" ||
    !identifier(value.job_id) ||
    !scopeSet.has(String(value.scope)) ||
    !(value.state === "QUEUED" || value.state === "RUNNING") ||
    typeof value.coalesced !== "boolean"
  ) throw new Error("API услугата върна невалиден sync enqueue отговор.");
  return value as unknown as SyncEnqueueResponse;
}

export function parseSyncState(value: unknown): SyncState {
  if (!isRecord(value) || !exactKeys(value, stateKeys))
    throw new Error("API услугата върна невалиден sync статус.");
  if (
    value.schema_version !== "sync-state-v1" ||
    !(value.job_id === null || identifier(value.job_id)) ||
    !(value.scope === null || scopeSet.has(String(value.scope))) ||
    !stateSet.has(String(value.state)) ||
    !nullableString(value.stage) ||
    typeof value.progress_percent !== "number" ||
    !Number.isFinite(value.progress_percent) ||
    value.progress_percent < 0 ||
    value.progress_percent > 100 ||
    !nullableTimestamp(value.requested_at) ||
    !nullableTimestamp(value.started_at) ||
    !nullableTimestamp(value.finished_at) ||
    !nullableTimestamp(value.retry_at) ||
    !nullableString(value.failure_code) ||
    !(value.active_generation_id === null || identifier(value.active_generation_id)) ||
    !Number.isInteger(value.active_revision) ||
    Number(value.active_revision) < 0 ||
    !(value.analysis_as_of === null || isCalendarDate(value.analysis_as_of)) ||
    !nullableTimestamp(value.activated_at)
  ) throw new Error("API услугата върна невалиден sync статус.");

  const idle = value.state === "IDLE";
  if ((idle && (value.job_id !== null || value.scope !== null)) || (!idle && (value.job_id === null || value.scope === null)))
    throw new Error("API услугата върна несъгласуван sync статус.");
  if ((value.active_generation_id === null) !== (value.active_revision === 0))
    throw new Error("API услугата върна несъгласувана активна версия.");
  if (value.active_generation_id === null && (value.analysis_as_of !== null || value.activated_at !== null))
    throw new Error("API услугата върна несъгласувана активна версия.");

  return value as unknown as SyncState;
}

export const syncInProgress = (state: SyncState) =>
  state.state === "QUEUED" || state.state === "RUNNING" || state.state === "RETRY_WAIT";

export const syncTerminal = (state: SyncState) =>
  state.state === "SUCCEEDED" || state.state === "FAILED" || state.state === "SUPERSEDED";

export const syncRequiresViewRefresh = (state: SyncState, renderedGenerationId: string | null) =>
  state.active_generation_id !== null && state.active_generation_id !== renderedGenerationId;

const POLL_DELAYS_MS = [2_000, 3_000, 5_000, 8_000, 10_000] as const;

export const syncPollDelay = (attempt: number) =>
  POLL_DELAYS_MS[Math.min(Math.max(0, attempt), POLL_DELAYS_MS.length - 1)];

export const syncScopeLabel = (scope: SyncScope | null) => scope === "WELLNESS"
  ? "wellness данните"
  : scope === "RECOVERY"
    ? "Recovery модела"
    : "тренировъчните данни";
