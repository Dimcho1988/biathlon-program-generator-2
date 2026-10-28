import type { PlanningDraft } from "./training-management";

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
