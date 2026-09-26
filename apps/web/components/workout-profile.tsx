import type { CSSProperties } from "react";
import { durationHms } from "../lib/duration-format";
import { COMPONENTS, type DraftSession } from "../lib/training-management";
import { blockHeight, componentColor, componentLabel, sessionSummary, visibleBlocks } from "../lib/training-visuals";

export function ComponentLegend() {
  return <div className="workout-legend" aria-label="Цветове на тренировъчните компоненти">{COMPONENTS.map(zone =>
    <span key={zone}><i style={{ background: componentColor(zone) }} aria-hidden="true"/>{componentLabel(zone)}</span>)}</div>;
}

export function WorkoutProfile({ session, compact = false }: { session: DraftSession; compact?: boolean }) {
  const blocks = visibleBlocks(session);
  const duration = blocks.reduce((sum, block) => sum + block.duration_min, 0);
  const hasStrength = blocks.some(block => block.zone === "STR");
  const hasAerobic = blocks.some(block => /^Z[1-5]$/.test(block.zone));
  if (!blocks.length || duration <= 0) return <span className="workout-profile-empty">Няма записана структура на тренировката.</span>;
  const lane = (strength: boolean) => <span className="workout-profile-bars" aria-hidden="true">{blocks.map((block, index) =>
    <span key={index} className="workout-profile-slot" style={{ width: `${block.duration_min / duration * 100}%` }}>
      {(block.zone === "STR") === strength && <span className={`workout-profile-bar ${block.kind === "TRANSITION" || (strength && block.kind === "RECOVERY") ? "is-pause" : ""}`}
        style={{ height: `${blockHeight(block)}%`, background: componentColor(block.zone) }} title={`${block.label} · ${durationHms(block.duration_min)} · ${componentLabel(block.zone)}`}/>}</span>)}</span>;
  return <span className={`workout-profile ${compact ? "is-compact" : ""}`} role="img" aria-label={`Структура: ${sessionSummary(session)}. ${blocks.map(b => `${b.label}: ${durationHms(b.duration_min)} ${componentLabel(b.zone)}`).join("; ")}`}>
    {hasAerobic && lane(false)}
    {hasStrength && <span className="workout-strength-lane"><span className="workout-lane-label">Сила</span>{lane(true)}</span>}
    {!hasAerobic && !hasStrength && lane(false)}
    <span className="workout-profile-axis" aria-hidden="true"><span>0:00:00</span><span>Време</span><span>{durationHms(duration)}</span></span>
  </span>;
}

export function SessionAccent({ zone }: { zone: string }) {
  return <span className="workout-target" style={{ "--session-color": componentColor(zone) } as CSSProperties}><i aria-hidden="true"/>Основна цел: {componentLabel(zone)}</span>;
}
