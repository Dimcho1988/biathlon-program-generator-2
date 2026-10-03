export type ChartPoint = { x: number; y: number | null; breakBefore?: boolean; partial?: boolean };

export function chartDomain(values: readonly (number | null)[], zero = false): [number, number] {
  let low = Infinity, high = -Infinity;
  for (const value of values) if (value !== null && Number.isFinite(value)) {
    low = Math.min(low, value); high = Math.max(high, value);
  }
  if (!Number.isFinite(low)) return [0, 1];
  const padding = Math.max((high - low) * .08, Math.abs(high) * .01, .1);
  return [zero && low >= 0 ? 0 : low - padding, high + padding];
}

export function chartTicks(low: number, high: number, count = 5, duration = false): number[] {
  if (high <= low) return [low];
  const raw = (high - low) / (count - 1);
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = (duration ? [1,2,5,10,15,30,60,120,180,300,600,900,1800,3600,7200,10800,14400,21600,43200,86400].find(value => value >= raw) : undefined)
    ?? [1, 2, 2.5, 5, 10].map(value => value * power).find(value => value >= raw) ?? power * 10;
  const ticks: number[] = [];
  for (let value = Math.ceil(low / step) * step; value <= high + step * .001; value += step) ticks.push(Number(value.toPrecision(12)));
  return ticks;
}

// Keep missing observations and time gaps visible; never invent a zero or interpolate them.
export function chartPath(points: readonly ChartPoint[], x: (value: number) => number, y: (value: number) => number, maxGap = Infinity): string {
  let previous: number | null = null;
  return points.map(point => {
    if (!Number.isFinite(point.x) || point.y === null || !Number.isFinite(point.y)) { previous = null; return ""; }
    const command = previous === null || point.breakBefore || point.x - previous > maxGap ? "M" : "L";
    previous = point.x;
    return `${command}${x(point.x).toFixed(2)},${y(point.y).toFixed(2)}`;
  }).join(" ");
}

export function nearestChartPoint(points: readonly { x: number }[], value: number): number {
  let low = 0, high = points.length - 1;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (points[middle].x < value) low = middle + 1; else high = middle;
  }
  return low > 0 && Math.abs(points[low - 1].x - value) <= Math.abs(points[low].x - value) ? low - 1 : low;
}

export function spacedChartTicks(values: number[], x: (value: number) => number, spacing = 72): number[] {
  const result: number[] = [];
  for (const value of values) if (!result.length || x(value) - x(result.at(-1)!) >= spacing) result.push(value);
  const last = values.at(-1);
  if (last !== undefined && result.at(-1) !== last) {
    if (result.length > 1 && x(last) - x(result.at(-1)!) < spacing) result.pop();
    if (!result.length || x(last) - x(result[0]) >= spacing) result.push(last);
  }
  return result;
}
