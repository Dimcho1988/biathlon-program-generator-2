import { COMPONENTS, daySessions, type Component, type ComponentAllocation, type DraftDay, type PlanOutcome, type PlanningDraft, type QExecution } from "./training-management";

export interface QComparisonRow {
  zone: Component; target: number | null; planned: number | null; actual: number | null;
  forecast: number | null; plannedCompleted: number | null; actualCompleted: number | null; actualEstimated: number | null;
  targetBasis: "DIRECT_Q" | "CANONICAL_E" | "UNKNOWN";
}
export interface QComparisonPeriod {
  start: string; end: string; includesForecast: boolean; rows: QComparisonRow[];
  actualAsOf: string; liveExecution: boolean;
}
export interface QComparisonOptions {
  planKind?: "DRAFT" | "APPROVED"; qExecution?: QExecution | null; outcomes?: PlanOutcome[]; today?: string;
}
const quantity = (v: unknown): number | null => typeof v === "number" && Number.isFinite(v) && v >= 0 ? v : null;
const sum = (values: Array<number | null>) => values.every(v => v !== null) ? values.reduce<number>((a, b) => a + (b ?? 0), 0) : null;
export const qPercent = (value: number | null, target: number | null) => value !== null && target !== null && target > 0 ? 100 * value / target : null;

function qValue(row: ComponentAllocation | undefined, key: "target" | "planned" | "actual") {
  if (!row) return null;
  const direct = `${key}_q` as "target_q" | "planned_q" | "actual_q";
  if (direct in row) return quantity(row[direct]);
  return row.basis === "DIRECT_Q" ? quantity(row[key]) : null;
}
const dayQ = (day: DraftDay, zone: Component) => day.status === "EXISTING_ACTIVITY" ? null
  : sum(daySessions(day).map(s => quantity(s.direct_equivalent_minutes?.[zone])));

function dates(start: string, end: string) {
  const result: string[] = [];
  for (let t = Date.parse(`${start}T12:00:00Z`); t <= Date.parse(`${end}T12:00:00Z`); t += 86400000)
    result.push(new Date(t).toISOString().slice(0, 10));
  return result;
}

export function planQComparison(plan: PlanningDraft, options: QComparisonOptions = {}): QComparisonPeriod[] {
  const execution = options.qExecution ?? plan.q_execution;
  const asOf = execution?.as_of ?? plan.generated_at?.slice(0, 10) ?? plan.start_date;
  const today = options.today ?? asOf;
  const actualDays = new Map(execution?.days.map(d => [d.date, d]) ?? []);
  const periods = plan.allocation?.segments?.length ? plan.allocation.segments : [{
    window_start: plan.allocation?.window_start ?? plan.start_date,
    window_end: plan.allocation?.window_end ?? plan.end_date, components: plan.allocation?.components ?? {},
  }];
  return periods.map(p => {
    const inPeriod = (key: string) => p.window_start <= key && key <= p.window_end;
    const prescribed = plan.days.filter(d => inPeriod(d.date) && plan.start_date <= d.date && d.date <= plan.end_date);
    const forecast = (plan.lookahead_days ?? []).filter(d => inPeriod(d.date) && d.date > plan.end_date);
    const actualKeys = dates(p.window_start, p.window_end < asOf ? p.window_end : asOf);
    return {start: p.window_start, end: p.window_end, includesForecast: forecast.length > 0,
      actualAsOf: asOf, liveExecution: !!execution,
      rows: COMPONENTS.map(zone => {
        const row = p.components[zone];
        const historical = new Map((options.outcomes ?? []).filter(o => inPeriod(o.date))
          .map(o => [o.date, quantity(o.planned_q?.[zone])]));
        const schedule = options.planKind === "APPROVED" ? new Map([...historical].filter(([d]) => d <= plan.end_date)) : new Map<string, number | null>();
        for (const d of prescribed) {
          if (d.status !== "EXISTING_ACTIVITY") schedule.set(d.date, dayQ(d, zone));
          else if (options.planKind === "APPROVED" && !schedule.has(d.date)) schedule.set(d.date, null);
        }
        const completed = [...schedule.keys()].filter(d => d < today && d < asOf);
        return {zone, target: qValue(row, "target"), targetBasis: row?.basis ?? "UNKNOWN",
          planned: sum([...schedule.values()]), forecast: sum(forecast.map(d => dayQ(d, zone))),
          actual: execution ? sum(actualKeys.map(d => quantity(actualDays.get(d)?.q[zone]))) : qValue(row, "actual"),
          actualEstimated: execution ? sum(actualKeys.map(d => quantity(actualDays.get(d)?.estimated_q?.[zone] ?? 0))) : null,
          plannedCompleted: completed.length ? sum(completed.map(d => schedule.get(d) ?? null)) : null,
          actualCompleted: completed.length ? sum(completed.map(d => quantity(actualDays.get(d)?.q[zone]))) : null};
      })};
  });
}
