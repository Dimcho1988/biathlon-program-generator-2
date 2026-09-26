import { durationHms } from "./duration-format";
import type { Component, DraftSession, SessionBlock } from "./training-management";

// All planning views share the app's theme-aware component palette.
export const COMPONENT_COLORS: Record<Component, string> = {
  Z1: "var(--zone-1)", Z2: "var(--zone-2)", Z3: "var(--zone-3)",
  Z4: "var(--zone-4)", Z5: "var(--zone-5)", STR: "var(--strength)",
};
export const componentColor = (zone: string) => COMPONENT_COLORS[zone as Component] ?? "var(--text-muted)";
export const componentLabel = (zone: string) => zone === "STR" ? "Сила" : zone || "Без зададена зона";
export const SPORT_LABELS: Record<string, string> = { Run: "Бягане", NordicSki: "Ски бягане", RollerSki: "Ролкови ски", Ride: "Колоездене", Walk: "Ходене", Hike: "Преход", Strength: "Сила" };

export function visibleBlocks(session: DraftSession): SessionBlock[] {
  return session.blocks.filter(b => Number.isFinite(b.duration_min) && b.duration_min > 0);
}

/** Ordinal zone steps, not a physiological load scale. Strength uses a separate lane. */
export function blockHeight(block: SessionBlock): number {
  if (block.zone === "STR") return block.kind === "WORK" ? 48 : 18;
  const zone = /^Z([1-5])$/.exec(block.zone);
  return zone ? 20 + Number(zone[1]) * 14 : 12;
}

const sameDuration = (a: SessionBlock, b: SessionBlock) => Math.abs(a.duration_min - b.duration_min) < 1 / 600;
const samePart = (a: SessionBlock, b: SessionBlock) => a.kind === b.kind && a.zone === b.zone && sameDuration(a, b)
  && a.target_hr_bpm === b.target_hr_bpm && a.target_speed_kmh === b.target_speed_kmh;
const partLabel = (b: SessionBlock) => `${durationHms(b.duration_min)} ${componentLabel(b.zone)}`;

/** Describe the saved sequence; never rebuild the workout from its method title. */
export function sessionSummary(session: DraftSession): string {
  const blocks = visibleBlocks(session);
  const works = blocks.filter(b => b.kind === "WORK");
  if (!works.length) return session.title;
  const main = blocks.slice(blocks.indexOf(works[0]), blocks.lastIndexOf(works.at(-1)!) + 1);
  const strength = works.filter(b => b.zone === "STR");
  if (strength.length) {
    const circuits = strength.map(b => /^Кръг (\d+):/.exec(b.label)?.[1]);
    const ids = [...new Set(circuits)];
    const uniform = strength.every(b => sameDuration(b, strength[0]));
    const equalCircuits = ids.every(id => circuits.filter(c => c === id).length === strength.length / ids.length);
    const label = !circuits.includes(undefined) && equalCircuits && uniform
      ? `${ids.length} кръга × ${strength.length / ids.length} упражнения · ${durationHms(strength[0].duration_min)} работа`
      : `${strength.length} силови части · ${durationHms(strength.reduce((sum, b) => sum + b.duration_min, 0))} работа`;
    return strength.length === works.length ? label : `Аеробна работа + ${label}`;
  }
  if (works.length === 1) return `${partLabel(works[0])} основна работа`;
  const recoveries = main.filter(b => b.kind === "RECOVERY");
  if (main.length === works.length * 2 - 1 && main.every((b, i) => b.kind === (i % 2 ? "RECOVERY" : "WORK"))
    && works.every(b => samePart(b, works[0])) && recoveries.every(b => samePart(b, recoveries[0]))) {
    return `${works.length} × ${partLabel(works[0])} / ${partLabel(recoveries[0])} между отсечките`;
  }
  if (main.every(b => b.kind === "WORK") && works.every(b => b.zone === works[0].zone)
    && works.every((b, i) => b.target_hr_bpm != null && (i === 0 || b.target_hr_bpm > works[i - 1].target_hr_bpm!))) {
    return `Постепенно · ${durationHms(works.reduce((sum, b) => sum + b.duration_min, 0))} ${componentLabel(works[0].zone)} · ${Math.round(works[0].target_hr_bpm!)} → ${Math.round(works.at(-1)!.target_hr_bpm!)} уд./мин`;
  }
  // Exact repeated patterns also cover alternating zones without explicit pauses.
  for (let size = 1; size <= Math.floor(main.length / 2); size++) {
    if (main.length % size === 0 && main.every((b, i) => samePart(b, main[i % size]))) {
      return `${main.length / size} × (${main.slice(0, size).map(partLabel).join(" + ")})`;
    }
  }
  const zones = [...new Set(works.map(b => componentLabel(b.zone)))];
  return `${works.length} работни части · ${zones.join(" → ")} · ${durationHms(works.reduce((sum, b) => sum + b.duration_min, 0))} работа`;
}
