/** A duration, not a time of day: hours must not wrap after 24. */
export function durationHms(minutes: number): string {
  if (!Number.isFinite(minutes) || minutes < 0) return "—";
  const seconds = Math.round(minutes * 60);
  return `${Math.floor(seconds / 3600)}:${String(Math.floor(seconds / 60) % 60).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}
