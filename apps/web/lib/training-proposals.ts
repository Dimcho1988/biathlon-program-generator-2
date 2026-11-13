import { isCalendarDate, isRecord } from "./training-status";
import { parsePersonalMethod, type PersonalMethod } from "./method-library";

export interface TrainingStructureDirective {
  id: string; name: string; mode: "FIX" | "REPEAT" | "SESSION"; start_date: string;
  days: Array<{ offset: number; sessions: Array<{ slot: number; method_ids: string[]; sport: string }> }>;
  source?: { start_date: string; end_date: string; plan_revisions: number[]; days?: Array<{ date: string; plan_revision: number }> };
}
export type TrainingProposal =
  | { action: "PERSONAL_METHOD"; method: PersonalMethod }
  | { action: "REMOVE_PERSONAL_METHOD"; method_id: string }
  | { action: "FIX_STRUCTURE" | "REPEAT_STRUCTURE"; source_start: string; source_end: string; target_start: string; name?: string; source_revisions?: number[]; expected_plan_revision?: number }
  | { action: "REMOVE_STRUCTURE"; directive_id: string };
export interface TrainingProposalState {
  revision: number; profile_revision: number; active_plan_revision: number | null;
  payload: { personal_methods: PersonalMethod[]; training_structure: TrainingStructureDirective[] };
}
export interface TrainingProposalPreview extends TrainingProposalState {
  preview: { proposal: TrainingProposal; summary: string; corrections: unknown[]; days: unknown[] };
}
const revision = (value: unknown) => Number.isSafeInteger(value) && Number(value) >= 0;
const id = (value: unknown) => typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$/.test(value);
export function addProposalDays(date: string, days: number) {
  if (!isCalendarDate(date)) return "";
  return new Date(Date.parse(`${date}T12:00:00Z`) + days * 86_400_000).toISOString().slice(0, 10);
}
export function parseTrainingProposal(value: unknown): TrainingProposal {
  if (!isRecord(value)) throw new Error("Невалидно предложение.");
  const allowed = value.action === "PERSONAL_METHOD" ? ["action", "method"] : value.action === "REMOVE_PERSONAL_METHOD" ? ["action", "method_id"]
    : value.action === "REMOVE_STRUCTURE" ? ["action", "directive_id"] : ["action", "source_start", "source_end", "target_start", "name", "source_revisions", "expected_plan_revision"];
  if (Object.keys(value).some(key => !allowed.includes(key))) throw new Error("Предложението съдържа неподдържани настройки.");
  if (value.action === "PERSONAL_METHOD") return { action: value.action, method: parsePersonalMethod(value.method) };
  if (value.action === "REMOVE_PERSONAL_METHOD" && typeof value.method_id === "string" && value.method_id.startsWith("PERSONAL-") && id(value.method_id)) return value as unknown as TrainingProposal;
  if (value.action === "REMOVE_STRUCTURE" && typeof value.directive_id === "string" && value.directive_id.length > 0 && value.directive_id.length <= 80) return value as unknown as TrainingProposal;
  if (["FIX_STRUCTURE", "REPEAT_STRUCTURE"].includes(String(value.action)) && isCalendarDate(value.source_start) && isCalendarDate(value.source_end) && isCalendarDate(value.target_start)
    && [6, 13].includes((Date.parse(value.source_end) - Date.parse(value.source_start)) / 86_400_000)
    && (value.action !== "FIX_STRUCTURE" || value.source_start === value.target_start)
    && (value.name === undefined || typeof value.name === "string" && value.name.length <= 120)
    && (value.expected_plan_revision === undefined || revision(value.expected_plan_revision))
    && (value.source_revisions === undefined || Array.isArray(value.source_revisions) && value.source_revisions.length <= 14 && value.source_revisions.every(revision))) return value as unknown as TrainingProposal;
  throw new Error("Избери запазен период от 7 или 14 дни и валидна начална дата.");
}
export function parseTrainingStructures(value: unknown): TrainingStructureDirective[] {
  if (!Array.isArray(value) || value.length > 100 || value.some(item => !isRecord(item) || typeof item.id !== "string" || !item.id || item.id.length > 80 || typeof item.name !== "string"
    || !["FIX", "REPEAT", "SESSION"].includes(String(item.mode)) || !isCalendarDate(item.start_date) || !Array.isArray(item.days) || item.days.length < 1 || item.days.length > 14
    || item.days.some((day, index) => !isRecord(day) || day.offset !== index || !Array.isArray(day.sessions) || day.sessions.length > 3
      || day.sessions.some(session => !isRecord(session) || !Number.isInteger(session.slot) || Number(session.slot) < 1 || Number(session.slot) > 3
        || !["Run", "NordicSki", "RollerSki"].includes(String(session.sport)) || !Array.isArray(session.method_ids) || session.method_ids.length < 1 || session.method_ids.length > 2 || !session.method_ids.every(method => typeof method === "string" && method.length > 0 && method.length <= 180)))))
    throw new Error("Запазените структури не можаха да се прочетат.");
  return value as TrainingStructureDirective[];
}
export function parseTrainingProposalState(value: unknown): TrainingProposalState {
  if (!isRecord(value) || !revision(value.profile_revision ?? value.revision) || !isRecord(value.payload) || !Array.isArray(value.payload.personal_methods)
    || (value.active_plan_revision != null && !revision(value.active_plan_revision))) throw new Error("Предложенията не можаха да се заредят.");
  const profileRevision = Number(value.profile_revision ?? value.revision);
  return { revision: profileRevision, profile_revision: profileRevision, active_plan_revision: value.active_plan_revision == null ? null : Number(value.active_plan_revision),
    payload: { personal_methods: value.payload.personal_methods.map(parsePersonalMethod), training_structure: parseTrainingStructures(value.payload.training_structure) } };
}
export function parseTrainingProposalPreview(value: unknown): TrainingProposalPreview {
  const state = parseTrainingProposalState(value);
  if (!isRecord(value) || !isRecord(value.preview) || typeof value.preview.summary !== "string" || !Array.isArray(value.preview.corrections) || !Array.isArray(value.preview.days))
    throw new Error("Липсва проверен вариант. Прегледай предложението отново.");
  return { ...state, preview: { ...value.preview, proposal: parseTrainingProposal(value.preview.proposal) } as TrainingProposalPreview["preview"] };
}
