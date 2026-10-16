import { isRecord } from "../lib/training-status";
import type { DraftDay, DraftSession } from "../lib/training-management";

const LABELS: Record<string, string> = {
  BUILD: "Развиваща", BUILDING: "Развиваща",
  MAINTAIN: "Поддържаща", MAINTENANCE: "Поддържаща", SUPPORTING: "Поддържаща",
  REENTRY: "Вработваща", RE_ENTRY: "Вработваща", RECOVERY: "Възстановителна",
};

export function SessionRole({ session, day }: { session: DraftSession; day: Pick<DraftDay, "period" | "cycle"> }) {
  const evidence = session.dose_evidence;
  const reentry = day.period === "RE_ENTRY" || day.period === "REENTRY"
    || day.cycle?.kind === "RE_ENTRY" || evidence.reentry_dose_policy === "MAINTENANCE_ROLE_BUDGET";
  const label = (role: string) => role === "RECOVERY" ? LABELS.RECOVERY
    : reentry && LABELS[role] ? LABELS.RE_ENTRY : LABELS[role] ?? "Неуточнена";
  // Saved per-part roles own a combination; the primary role cannot describe
  // a secondary component with a different dose. Recovery weeks are not roles.
  const parts = Array.isArray(evidence.combined_budgets) ? evidence.combined_budgets.filter(part =>
    isRecord(part) && typeof part.zone === "string" && typeof part.role === "string") : [];
  const role = session.purpose === "RECOVERY" ? "RECOVERY" : evidence.resolved_role ?? session.purpose;
  const labels = parts.length ? parts.map(part => `${part.zone === "STR" ? "Сила" : part.zone} · ${label(part.role)}`)
    : evidence.mixed_component && session.purpose === "SUPPORTING"
      ? [...new Set(session.blocks.filter(block => block.kind === "WORK").map(block => block.zone))]
        .map(zone => `${zone === "STR" ? "Сила" : zone} · ${label("SUPPORTING")}`)
      : [label(role)];
  return <span className="management-session-roles" role="group" aria-label="Роля на тренировката">
    <span className="management-role-caption">Роля:</span>
    {(labels.length ? labels : [label(role)]).map((value, index) => <span className="management-session-role" key={index}>{value}</span>)}
  </span>;
}
