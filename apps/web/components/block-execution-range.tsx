import type { SessionBlock } from "../lib/training-management";

const pace = (seconds: number) => { const rounded = Math.round(seconds); return `${Math.floor(rounded / 60)}:${String(rounded % 60).padStart(2, "0")}`; };
export function BlockExecutionRange({ block }: { block: SessionBlock }) {
  const speeds = block.target_speed_range_kmh;
  if (!speeds) return null;
  const paces = block.target_pace_range_seconds_km ?? [3600 / speeds[1], 3600 / speeds[0]];
  const equivalent = block.distance_basis === "FLAT_EQUIVALENT" || block.speed_basis === "FLAT_EQUIVALENT" || block.primary_control === "EFFORT_ON_TERRAIN";
  return <small>
    {equivalent ? "Равнинен еквивалент" : "Ориентир"}: {paces.map(pace).join("–")} мин/км · {speeds.map(v => v.toLocaleString("bg-BG", { maximumFractionDigits: 1 })).join("–")} км/ч.
    {block.distance_range_m && <> {equivalent ? "Еквивалентна дистанция" : "Ориентировъчна дистанция"}: {block.distance_range_m.map(v => Math.round(v).toLocaleString("bg-BG")).join("–")} м за работна част.</>}
    {equivalent && <> По терена водещи са времето, усилието и техниката.</>}
  </small>;
}
