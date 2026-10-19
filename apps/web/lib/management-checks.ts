import { isRecord } from "./training-status";

export interface PlanCheckReceipt {
  operation: "CHECK_NO_CHANGE";
  check_id: string;
  checked_revision: number;
  recorded_at: string;
}

export interface PlanCheckEvent {
  id: string;
  sequence: number;
  checked_revision: number;
  operation: "CHECK_NO_CHANGE";
  recorded_at: string;
  reason: string;
}

export interface PlanChecksResponse { checks: PlanCheckEvent[]; next_before: number | null }

const positiveInteger = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) > 0;
const uuid = (value: unknown): value is string => typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
const zonedTimestamp = (value: unknown): value is string => typeof value === "string"
  && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value)
  && Number.isFinite(Date.parse(value));

export function parsePlanCheckReceipt(value: unknown): PlanCheckReceipt {
  if (!isRecord(value) || value.operation !== "CHECK_NO_CHANGE" || !uuid(value.check_id)
    || !positiveInteger(value.checked_revision) || !zonedTimestamp(value.recorded_at))
    throw new Error("Невалиден резултат от проверката на програмата.");
  return value as unknown as PlanCheckReceipt;
}

export function parsePlanChecks(value: unknown): PlanChecksResponse {
  if (!isRecord(value) || !Array.isArray(value.checks) || value.checks.length > 50
    || (value.next_before !== null && !positiveInteger(value.next_before)))
    throw new Error("Проверките на програмата не са достъпни.");
  let previous = Number.POSITIVE_INFINITY;
  const ids = new Set<string>();
  for (const check of value.checks) {
    if (!isRecord(check) || !uuid(check.id) || ids.has(check.id.toLowerCase()) || !positiveInteger(check.sequence)
      || check.sequence >= previous || !positiveInteger(check.checked_revision)
      || check.operation !== "CHECK_NO_CHANGE" || !zonedTimestamp(check.recorded_at)
      || typeof check.reason !== "string" || !check.reason.trim() || check.reason.length > 500)
      throw new Error("Невалидна история на проверките на програмата.");
    previous = check.sequence;
    ids.add(check.id.toLowerCase());
  }
  if (value.next_before !== null && (value.checks.length === 0 || value.next_before !== previous))
    throw new Error("Невалидна следваща страница с проверки.");
  return value as unknown as PlanChecksResponse;
}
