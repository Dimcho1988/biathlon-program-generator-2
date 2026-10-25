import { COMPONENTS, daySessions, type Component, type ComponentAllocation, type PlanningDraft } from "./training-management";

export interface QComparisonRow {
  zone: Component; target: number | null; planned: number | null; actual: number | null;
  targetBasis: "DIRECT_Q" | "CANONICAL_E" | "UNKNOWN";
}
export interface QComparisonPeriod {
  start: string; end: string; includesForecast: boolean; rows: QComparisonRow[];
}
const quantity = (v: unknown): number | null => typeof v === "number" && Number.isFinite(v) && v >= 0 ? v : null;

function qValue(row: ComponentAllocation | undefined, key: "target" | "planned" | "actual") {
  if (!row) return null;
  const direct = `${key}_q` as "target_q" | "planned_q" | "actual_q";
  // An explicit null is missing evidence. Never replace it with canonical E.
  if (direct in row) return quantity(row[direct]);
  return row.basis === "DIRECT_Q" ? quantity(row[key]) : null;
}

export function planQComparison(plan: PlanningDraft): QComparisonPeriod[] {
  const allocation = plan.allocation;
  if (!allocation) {
    const sessions = plan.days.filter(d => plan.start_date <= d.date && d.date <= plan.end_date).flatMap(daySessions);
    return [{start: plan.start_date, end: plan.end_date, includesForecast: false,
      rows: COMPONENTS.map(zone => {
        const values = sessions.map(s => quantity(s.direct_equivalent_minutes?.[zone]));
        return {zone, target: null, actual: null, targetBasis: "UNKNOWN",
          planned: values.every(v => v !== null) ? values.reduce<number>((sum, v) => sum + (v ?? 0), 0) : null};
      })}];
  }
  const periods = allocation.segments?.length ? allocation.segments : [{
    window_start: allocation.window_start ?? plan.start_date,
    window_end: allocation.window_end ?? plan.end_date, components: allocation.components,
  }];
  return periods.map(p => ({ start: p.window_start, end: p.window_end,
    includesForecast: p.window_end > plan.end_date,
    rows: COMPONENTS.map(zone => {
      const row = p.components[zone];
      return { zone, target: qValue(row, "target"), planned: qValue(row, "planned"), actual: qValue(row, "actual"),
        targetBasis: row?.basis ?? "UNKNOWN" };
    }),
  }));
}
