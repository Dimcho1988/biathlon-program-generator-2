import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { RaceCourseEditor } from "../components/race-course-editor";
import { BlockExecutionRange } from "../components/block-execution-range";
import { RaceDurationSummary } from "../components/race-duration-estimate";
import { parsePlanningCalendarInput, parseRaceCourse, type RaceCourse } from "../lib/planning-calendar";
import { blockDurationLabel, sessionSummary } from "../lib/training-visuals";
import type { DraftSession, SessionBlock } from "../lib/training-management";

const course: RaceCourse = { distance_m: 6000, surface: "TRAIL", profile: "UPHILL", gradient_percent: [5, 7], expected_duration_min: [22, 26], specific_sessions_percent: 70, familiar_terrain: true };
const work: SessionBlock = { kind: "WORK", zone: "Z3", duration_min: 4, duration_range_seconds: [216, 240],
  target_hr_bpm: null, target_speed_kmh: 18, target_speed_range_kmh: [3600/210, 18], target_pace_range_seconds_km: [200, 210],
  distance_range_m: [1028.571, 1200], label: "Отсечка", repetition: 1, instructions: "", distance_basis: "FLAT_EQUIVALENT" };

describe("race course and time-first ranges", () => {
  it("preserves explicit race fields in the existing calendar contract", () => {
    const calendar = { schema_version: "planning-calendar-v1", events: [{ event_id: "race-main-001", event_type: "MAIN_RACE", name: "Старт", start_date: "2026-10-24", end_date: "2026-10-24", race_course: course }] };
    expect(parsePlanningCalendarInput(calendar)).toEqual(calendar);
    expect(() => parsePlanningCalendarInput({ ...calendar, events: [{ ...calendar.events[0], event_type: "CAMP" }] })).toThrow(/само към състезание/);
    expect(parsePlanningCalendarInput({ ...calendar, events: [{ ...calendar.events[0], race_course: undefined }] }).events[0].race_course).toBeUndefined();
  });
  it.each([{ distance_m: NaN }, { surface: ["TRAIL"] }, { gradient_percent: [7, 5] }, { expected_duration_min: [20] }, { surface: "TRACK" }, { familiar_terrain: "yes" }, { ascent_m: null }])("rejects invalid course data %j", change => {
    expect(() => parseRaceCourse({ ...course, ...change })).toThrow();
  });
  it("renders clear independent fields and the taper familiarity control", () => {
    const html = renderToStaticMarkup(<RaceCourseEditor value={course} onChange={() => {}}/>);
    for (const text of ["Дистанция (м)", "Само изкачване", "Наклон (%) от", "Наклон (%) до", "Очаквана продължителност (мин)", "и в тейпъра"]) expect(html).toContain(text);
    expect(html).toContain('value="6000"');
  });
  it("displays time and pace ranges with a distance equivalent rather than an uphill speed target", () => {
    expect(blockDurationLabel(work)).toBe("0:03:36–0:04:00");
    const html = renderToStaticMarkup(<BlockExecutionRange block={work}/>);
    expect(html).toContain("3:20–3:30 мин/км");
    expect(html).toContain("Равнинен еквивалент");
    expect(html).toContain("Еквивалентна дистанция");
    expect(html).toContain("времето, усилието и техниката");
    const recovery = { ...work, kind: "RECOVERY", duration_min: 3, duration_range_seconds: undefined };
    const session = { blocks: [work, recovery, work, recovery, work], title: "Моделна", zone: "Z3" } as DraftSession;
    expect(sessionSummary(session)).toContain("3 × 0:03:36–0:04:00");
    expect(blockDurationLabel({ ...work, duration_range_seconds: undefined })).toBe("0:04:00");
  });
  it("labels the race time as a coach range for this course", () => {
    const html = renderToStaticMarkup(<RaceDurationSummary value={{ duration_min: 24, duration_range_min: [22, 26], source: "COURSE_EXPECTATION", specific_reference: { course, bands: [] } }}/>);
    expect(html).toContain("0:22:00–0:26:00");
    expect(html).toContain("за конкретното трасе");
    expect(html).toContain("равнинен еквивалент");
  });
});
