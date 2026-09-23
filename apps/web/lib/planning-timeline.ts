import { isCalendarDate, isRecord } from "./training-status";
import { PHASE_LABELS, type CycleDirective, type PlanProjection } from "./training-management";
import type { PlanningCalendarEvent, PlanningEventType } from "./planning-calendar";

export const EVENT_LABELS: Record<PlanningEventType, string> = { MAIN_RACE: "Основен старт", CONTROL_RACE: "Контролен старт", CAMP: "Лагер", TEST: "Тест", UNAVAILABLE: "Без тренировки" };
export const CYCLE_LABELS: Record<CycleDirective["kind"], string> = { BUILD: "Изграждащ блок", MAINTAIN: "Поддържащ блок", STRESS: "Стресов микроцикъл", RECOVERY: "Разтоварване" };
export interface TimelineItem { id: string; start: string; end: string; label: string; detail: string; tone: string; lane: "Периоди" | "Акценти" | "Мои блокове" | "Събития" }
export const shiftDay = (day: string, n: number) => new Date(Date.parse(`${day}T12:00:00Z`) + n * 86400000).toISOString().slice(0, 10);
export const shortDay = (day: string) => `${day.slice(8, 10)}.${day.slice(5, 7)}.${day.slice(0, 4)}`;
const validPeriod = (start: unknown, end: unknown): start is string => isCalendarDate(start) && isCalendarDate(end) && start <= end;

export function timelineItems(events: PlanningCalendarEvent[], cycles: CycleDirective[], plan?: PlanProjection | null): TimelineItem[] {
  const items: TimelineItem[] = events.filter(e => validPeriod(e.start_date, e.end_date)).map(e => ({ id: e.event_id, start: e.start_date, end: e.end_date, label: e.name || EVENT_LABELS[e.event_type], detail: EVENT_LABELS[e.event_type], tone: e.event_type.toLowerCase(), lane: "Събития" }));
  cycles.forEach((c, i) => {
    if (!validPeriod(c.start_date, c.end_date)) return;
    items.push({ id: `cycle-${i}`, start: c.start_date, end: c.end_date, label: c.name, detail: `${CYCLE_LABELS[c.kind]} · ${c.accents.join(", ")} · цел 7/40: ${c.target_index}`, tone: c.kind.toLowerCase(), lane: "Мои блокове" });
    if (c.kind === "STRESS") items.push({ id: `recovery-${i}`, start: shiftDay(c.end_date, 1), end: shiftDay(c.end_date, c.recovery_days), label: "Разтоварване след стреса", detail: `${c.recovery_days} дни · ${c.name}`, tone: "recovery", lane: "Мои блокове" });
  });
  const periods = isRecord(plan?.periodization) ? plan.periodization : {};
  for (const p of Array.isArray(periods.phases) ? periods.phases.filter(isRecord) : []) {
    if (!validPeriod(p.start_date, p.end_date)) continue;
    items.push({ id: `phase-${p.start_date}-${p.kind}`, start: p.start_date, end: String(p.end_date), label: PHASE_LABELS[String(p.kind)] ?? String(p.kind), detail: "План по запазените настройки", tone: "phase", lane: "Периоди" });
  }
  for (const p of Array.isArray(periods.taper_windows) ? periods.taper_windows.filter(isRecord) : []) {
    if (!validPeriod(p.start_date, p.end_date)) continue;
    items.push({ id: `taper-${p.start_date}`, start: p.start_date, end: String(p.end_date), label: "Тейпър", detail: "Намаляване преди основен старт", tone: "recovery", lane: "Периоди" });
  }
  const longTerm = isRecord(plan?.long_term) ? plan.long_term : {};
  for (const w of Array.isArray(longTerm.weeks) ? longTerm.weeks.filter(isRecord) : []) {
    if (!validPeriod(w.start_date, w.end_date)) continue;
    const cycle = isRecord(w.cycle) ? w.cycle : {};
    items.push({ id: `week-${w.start_date}`, start: w.start_date, end: String(w.end_date), label: Array.isArray(w.accents) ? w.accents.join(" · ") : "Без акцент", detail: `${String(cycle.name ?? "Седмична вълна")} · план по запазените настройки`, tone: cycle.kind === "RECOVERY" ? "recovery" : cycle.kind === "STRESS" ? "stress" : "accent", lane: "Акценти" });
  }
  return items;
}

// Clip inclusive date intervals to the view; retain overlaps on separate rows.
export function timelineRows(items: TimelineItem[], start: string, end: string) {
  const span = Date.parse(end) - Date.parse(start) + 86400000;
  const rows: Array<Array<TimelineItem & { left: number; width: number }>> = [];
  for (const item of [...items].filter(i => i.start <= end && i.end >= start).sort((a, b) => a.start.localeCompare(b.start) || a.id.localeCompare(b.id))) {
    const first = item.start < start ? start : item.start, last = item.end > end ? end : item.end;
    let row = rows.find(r => r[r.length - 1].end < first);
    if (!row) { row = []; rows.push(row); }
    row.push({ ...item, left: 100 * (Date.parse(first) - Date.parse(start)) / span, width: 100 * (Date.parse(last) - Date.parse(first) + 86400000) / span });
  }
  return rows;
}
