import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { WorkoutProfile } from "../components/workout-profile";
import { TrainingPlanWeek } from "../components/training-plan-week";
import { blockHeight, sessionSummary } from "../lib/training-visuals";
import type { DraftDay, DraftSession, SessionBlock } from "../lib/training-management";

const block = (kind: string, zone: string, duration_min: number, extra: Partial<SessionBlock> = {}): SessionBlock => ({
  kind, zone, duration_min, label: kind, instructions: "", repetition: null, target_hr_bpm: null, target_speed_kmh: null, ...extra,
});
const session = (blocks: SessionBlock[], zone = "Z4"): DraftSession => ({
  title: "Тестова тренировка", sport: "Run", zone, method_id: "test", purpose: "BUILDING", blocks,
  total_minutes: blocks.reduce((s, b) => s + b.duration_min, 0),
  main_work_minutes: blocks.filter(b => b.kind === "WORK").reduce((s, b) => s + b.duration_min, 0),
  canonical_effective_load: {} as DraftSession["canonical_effective_load"], direct_equivalent_minutes: {} as DraftSession["direct_equivalent_minutes"],
  dose_evidence: {} as DraftSession["dose_evidence"],
});

describe("saved workout visualization", () => {
  it("preserves five work intervals and only four recoveries, using elapsed time", () => {
    const blocks = [block("WARMUP", "Z1", 15), ...Array.from({ length: 5 }, (_, i) => [block("WORK", "Z4", 3), ...(i < 4 ? [block("RECOVERY", "Z1", 2)] : [])]).flat(), block("COOLDOWN", "Z1", 10)];
    const s = session(blocks);
    expect(sessionSummary(s)).toBe("5 × 0:03:00 Z4 / 0:02:00 Z1 между отсечките");
    const html = renderToStaticMarkup(<WorkoutProfile session={s}/>);
    expect(html.match(/class="workout-profile-slot"/g)).toHaveLength(11);
    expect(html.match(/background:var\(--zone-4\)/g)).toHaveLength(5);
    expect(html).toContain("0:48:00");
    expect(html).toContain("width:31.25%");
    expect(s.blocks).toEqual(blocks);
  });
  it("distinguishes contiguous progression from intervals and detects zone alternation", () => {
    expect(sessionSummary(session([140, 150, 160].map(hr => block("WORK", "Z2", 10, { target_hr_bpm: hr })), "Z2")))
      .toBe("Постепенно · 0:30:00 Z2 · 140 → 160 уд./мин");
    expect(sessionSummary(session(Array.from({length: 3}, () => [block("WORK", "Z2", 10), block("WORK", "Z1", 5)]).flat(), "Z2")))
      .toBe("3 × (0:10:00 Z2 + 0:05:00 Z1)");
  });
  it("does not invent a regular interval pattern for uneven efforts or recoveries", () => {
    const s = session([block("WORK", "Z3", 20), block("RECOVERY", "Z1", 3), block("WORK", "Z5", 1), block("RECOVERY", "Z1", 2), block("WORK", "Z5", .5)], "Z3");
    expect(sessionSummary(s)).toBe("3 работни части · Z3 → Z5 · 0:21:30 работа");
    expect(blockHeight(s.blocks[0])).toBeLessThan(blockHeight(s.blocks[2]));
  });
  it("keeps strength on a separate lane, including transitions and recovery", () => {
    const s = session([block("WARMUP", "Z1", 5), ...[1, 2].flatMap(circuit => [
      block("WORK", "STR", 1 / 3, {label: `Кръг ${circuit}: Клек`}), block("TRANSITION", "STR", .5),
      block("WORK", "STR", 1 / 3, {label: `Кръг ${circuit}: Напад`}),
      ...(circuit === 1 ? [block("RECOVERY", "STR", 2)] : []),
    ])], "STR");
    expect(sessionSummary(s)).toBe("2 кръга × 2 упражнения · 0:00:20 работа");
    const html = renderToStaticMarkup(<WorkoutProfile session={s}/>);
    expect(html).toContain('class="workout-strength-lane"');
    expect(html).toContain("var(--strength)");
    expect(html.match(/workout-profile-bar is-pause/g)).toHaveLength(3);
  });
  it("handles missing or zero duration blocks without fake structure or invalid geometry", () => {
    expect(renderToStaticMarkup(<WorkoutProfile session={session([])}/>)).toContain("Няма записана структура");
    const html = renderToStaticMarkup(<WorkoutProfile session={session([block("WORK", "UNKNOWN", 1), block("WORK", "Z5", 0)])}/>);
    expect(html).not.toContain("NaN");
    expect(html).not.toContain("Infinity");
    expect(html).toContain("var(--text-muted)");
  });
  it("shows each daily session and its target even when easy work is longer", () => {
    const first = session([block("WARMUP", "Z1", 20), block("WORK", "Z4", 5)]);
    const second = session([block("WORK", "Z2", 30)], "Z2");
    const day = { date: "2026-09-26", status: "TRAINING", session: first, sessions: [first, second] } as DraftDay;
    const html = renderToStaticMarkup(<TrainingPlanWeek days={[day, { ...day, date: "2026-09-27", status: "REST", session: null, sessions: [] }]} today={day.date} renderDay={() => <p>Подробности</p>}/>);
    expect(html).toContain("2 сесии");
    expect(html).toContain("0:55:00");
    expect(html).toContain("Основна цел: Z4");
    expect(html).toContain("Основна цел: Z2");
    expect(html).toContain("Почивка");
    expect(html.match(/class="management-workout-session"/g)).toHaveLength(2);
  });
});
