import { COMPONENTS, daySessions, type Component, type PlanningDraft } from "./training-management";

/** Count prescribed clock time once, from all session blocks, never from E.
 * Missing future days are unknown. Recorded rest days are known zeroes.
 */
export function microcycleVolume(plan: PlanningDraft | undefined, start: string, end: string) {
  const days = plan?.days.filter(d => d.date >= start && d.date <= end
    && (daySessions(d).length > 0 || ["REST", "SKIPPED", "UNAVAILABLE"].includes(d.status))) ?? [];
  const expectedDays = Math.round((Date.parse(end) - Date.parse(start)) / 86400000) + 1;
  const components = Object.fromEntries(COMPONENTS.map(z => [z, 0])) as Record<Component, number>;
  let total = 0, unallocated = 0, valid = true;
  for (const day of days) for (const session of daySessions(day)) {
    let assigned = 0;
    total += session.total_minutes;
    for (const block of session.blocks) {
      if (!Number.isFinite(block.duration_min) || block.duration_min < 0) { valid = false; continue; }
      if (COMPONENTS.includes(block.zone as Component)) {
        components[block.zone as Component] += block.duration_min;
        assigned += block.duration_min;
      }
    }
    if (assigned > session.total_minutes + .05) valid = false;
    unallocated += Math.max(0, session.total_minutes - assigned);
  }
  const coveredDays = new Set(days.map(d => d.date)).size;
  return { components, total, unallocated, coveredDays, expectedDays,
    complete: coveredDays === expectedDays, available: coveredDays > 0 && valid };
}
