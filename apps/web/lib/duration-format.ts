/** A duration, not a time of day: hours must not wrap after 24. */
export function durationSeconds(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return "—";
  const rounded = Math.round(seconds);
  return `${Math.floor(rounded / 3600)}:${String(Math.floor(rounded / 60) % 60).padStart(2, "0")}:${String(rounded % 60).padStart(2, "0")}`;
}

export function durationHms(minutes: number | null | undefined): string {
  return durationSeconds(minutes == null ? minutes : minutes * 60);
}

/** Signed time differences are distinct from non-negative durations. */
export function durationDelta(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  const rounded = Math.round(Math.abs(seconds));
  return `${rounded === 0 ? "" : seconds < 0 ? "−" : "+"}${durationSeconds(rounded)}`;
}
