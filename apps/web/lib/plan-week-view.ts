import type { ActivePlanRecord, PlanningDraft } from "./training-management";
import { isRecord } from "./training-status";

export interface PlanWeekRange { start: string; end: string }

export const planDateLabel = (date: string) => `${date.slice(8, 10)}.${date.slice(5, 7)}.${date.slice(0, 4)}`;
export const planRangeLabel = (week: PlanWeekRange) => `${planDateLabel(week.start)} – ${planDateLabel(week.end)}`;

export function planDates(start: string, end: string) {
  const dates: string[] = [];
  for (let t = Date.parse(`${start}T12:00:00Z`); t <= Date.parse(`${end}T12:00:00Z`); t += 86400000)
    dates.push(new Date(t).toISOString().slice(0, 10));
  return dates;
}

export function planWeekRanges(plan: PlanningDraft): PlanWeekRange[] {
  return plan.allocation?.segments?.length ? plan.allocation.segments.map(s => ({start:s.window_start, end:s.window_end}))
    : [{start:plan.allocation?.window_start ?? plan.start_date, end:plan.allocation?.window_end ?? plan.end_date}];
}

/** The model's anchored current cycle and its next cycle, never new calendar anchors. */
export function availablePlanWeeks(plan: PlanningDraft, today: string) {
  const ranges = planWeekRanges(plan);
  let index = ranges.findIndex(w => w.start <= today && today <= w.end);
  if (index < 0) index = ranges.findIndex(w => w.start > today);
  if (index < 0) index = ranges.length - 1;
  return ranges.slice(index, index + 2);
}

export function selectedPlanWeek(plan: PlanningDraft, today: string, selected?: string | null) {
  const weeks = availablePlanWeeks(plan, today);
  return weeks.find(w => w.start === selected) ?? weeks[0];
}

export function isAdaptivePlan(plan: { input_snapshot?: unknown }) {
  const snapshot = plan.input_snapshot;
  return isRecord(snapshot) && isRecord(snapshot.management_profile) && snapshot.management_profile.adaptation_mode === "AUTO";
}

/** Schedule windows are separate from the model's anchored accounting cycles. */
export function availableScheduleWindows(plan: PlanningDraft, today: string): PlanWeekRange[] {
  if (!isAdaptivePlan(plan)) return availablePlanWeeks(plan, today);
  const first = plan.start_date > today ? plan.start_date : today;
  const add = (day: string, n: number) => new Date(Date.parse(`${day}T12:00:00Z`)+n*86400000).toISOString().slice(0,10);
  const horizon = plan.parameters?.horizon;
  const end = isRecord(horizon) && typeof horizon.effective_end === "string" ? horizon.effective_end : add(first,13);
  if (end < first) return [{start:first,end:first}];
  return [0,7].filter(n => add(first,n) <= end).map(n => ({start:add(first,n),end:add(first,n+6) < end ? add(first,n+6) : end}));
}

export function selectedScheduleWindow(plan: PlanningDraft, today: string, selected?: string | null) {
  const windows = availableScheduleWindows(plan, today);
  return windows.find(w => w.start === selected) ?? windows[0];
}

/** Primary days win over aliases or overlapping lookahead records. Missing days stay missing. */
export function planWeekView(plan: PlanningDraft, week: PlanWeekRange) {
  const inWeek = (date: string) => week.start <= date && date <= week.end;
  const primary = new Map(plan.days.filter(d => inWeek(d.date) && plan.start_date <= d.date && d.date <= plan.end_date).map(d => [d.date,d]));
  const forecast = new Map((plan.lookahead_days ?? []).filter(d => inWeek(d.date) && d.date > plan.end_date && !primary.has(d.date)).map(d => [d.date,d]));
  const days = [...primary.values(), ...forecast.values()].sort((a,b) => a.date.localeCompare(b.date));
  const dates = planDates(week.start,week.end);
  return {days, forecastDates:[...forecast.keys()],
    missingDates:dates.filter(date => date >= plan.start_date && !primary.has(date) && !forecast.has(date)),
    historyDates:dates.filter(date => date < plan.start_date && !primary.has(date)),
    programmeDates:[...primary.keys()]};
}

/** Keep the approved schedule authoritative and fill only its uncovered future dates. */
export function activeScheduleView(record: ActivePlanRecord, week: PlanWeekRange, showProposal: boolean) {
  const { plan, proposal, mode, status, proposal_mode } = record.payload;
  const schedule = planWeekView(showProposal && proposal ? proposal : plan, week);
  if (showProposal || record.stale || mode !== "AUTO" || status !== "REVIEW_REQUIRED"
    || !proposal || proposal.status === "BLOCKED" || proposal_mode === "REVIEW"
    || !isAdaptivePlan(plan) || !isAdaptivePlan(proposal) || !schedule.missingDates.length) {
    return { ...schedule, proposalDates: [] as string[] };
  }
  const extension = planWeekView(proposal, week).days.filter(day =>
    day.date > plan.end_date && schedule.missingDates.includes(day.date));
  const proposalDates = extension.map(day => day.date);
  return {
    ...schedule,
    days: [...schedule.days, ...extension].sort((a, b) => a.date.localeCompare(b.date)),
    forecastDates: [...schedule.forecastDates, ...proposalDates],
    missingDates: schedule.missingDates.filter(date => !proposalDates.includes(date)),
    proposalDates,
  };
}
