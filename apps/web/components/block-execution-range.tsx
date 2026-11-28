"use client";
import { isRawMovingSpeed, speedBasisLabel } from "../lib/speed-basis";
import { speedRangeText } from "../lib/speed-format";
import { useRunningSpeedUnit } from "./running-speed-unit";
import type { SessionBlock } from "../lib/training-management";

export function BlockExecutionRange({ block, sport }: { block: SessionBlock; sport?: string }) {
  const unit = useRunningSpeedUnit();
  const speeds = block.target_speed_range_kmh;
  if (!speeds) return null;
  const raw = isRawMovingSpeed(block);
  const equivalent = !raw && (block.distance_basis === "FLAT_EQUIVALENT" || block.speed_basis === "FLAT_EQUIVALENT" || block.primary_control === "EFFORT_ON_TERRAIN");
  return <small>
    {raw ? speedBasisLabel(block) : equivalent ? "Равнинен еквивалент" : "Ориентир"}: {speedRangeText(speeds, unit, sport)}.
    {block.distance_range_m && <> {equivalent ? "Еквивалентна дистанция" : "Ориентировъчна дистанция"}: {block.distance_range_m.map(v => Math.round(v).toLocaleString("bg-BG")).join("–")} м за работна част.</>}
    {raw && <> Без корекция за релефа; сравнявай при сходни условия.</>}
    {equivalent && <> По терена водещи са времето, усилието и техниката.</>}
  </small>;
}
