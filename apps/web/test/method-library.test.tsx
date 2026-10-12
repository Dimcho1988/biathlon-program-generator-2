import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MethodCard, TrainingMethodLibrary } from "../components/training-method-library";
import { SessionMethodGuidance, sessionMethods } from "../components/session-method-guidance";
import { methodPaceText, parseMethodLibrary, parseMethodPreferences, type MethodLibraryResponse, type LibraryMethod } from "../lib/method-library";
import { defaultManagementProfile, parseManagementProfile, type DraftSession } from "../lib/training-management";

export const testMethod: LibraryMethod = {
  id: "XC01", title: "Повторения по основна дисциплина", sports: ["NordicSki", "RollerSki"], periods: ["SPECIAL_PREPARATION"], disciplines: [10000],
  source: { authors: "Laursen & Buchheit", reference: "Science and Application of HIIT", locator: "с. 254", links: ["https://doi.org/example"] },
  original: { work: "4 × 8 мин", anchor: "90–92% HRmax" }, adaptation: { notes: "Работен диапазон по КС; експертна адаптация." },
  guidance: { purpose: "Равномерен ритъм и техника.", execution: "Запази ритъма.", recovery: "Леко движение.", success: "Еднакво изпълнение.", errors: "Прекалено бързо начало.", adjustment: "Спри при загуба на техника.", progression: "По-дълга отсечка при по-нисък процент." },
  automatic_selection: true,
  prescription: { anchor: { type: "CV" }, speed_multiplier: [.98, 1.02], work: { unit: "seconds", range: [240, 480] }, repetitions: [3, 5], sets: { count: 2, recovery_seconds: [180, 240] }, recovery: { seconds: [120, 180], type: "ACTIVE_Z1" } },
  resolution: { status: "READY", phase: "SPECIAL_PREPARATION", anchor: { type: "CV", speed_kmh: 18 }, target_speed_range_kmh: [17.64, 18.36], work_duration_range_seconds: [240, 480], speed_role: "FLAT_EQUIVALENT_REFERENCE_NOT_TERRAIN_PACE" },
};
export const testLibrary: MethodLibraryResponse = { version: "training-method-library-v1", methods: [testMethod], configured: true, revision: 7, preferences: { enabled: true, overrides: {} }, profile: { sport: "NordicSki", actual_sport: "NordicSki", discipline: "10 km" }, phase: "SPECIAL_PREPARATION" };

describe("source-preserving method library", () => {
  it("accepts the real catalog, including numeric disciplines and structured series", () => {
    const catalog = JSON.parse(readFileSync(new URL("../../../biathlon/data/training_method_library.json", import.meta.url), "utf8"));
    const parsed = parseMethodLibrary({ ...testLibrary, methods: catalog.methods });
    expect(parsed.methods.length).toBeGreaterThan(100);
    const html = renderToStaticMarkup(<TrainingMethodLibrary initial={parsed} athleteName="Дими" canEdit selectedId="R01"/>);
    expect(html).toContain("База от тренировъчни методи");
    expect(html).not.toContain("[object Object]");
  });
  it("shows source, adaptation, full instructions, paired ranges and personal pace", () => {
    const html = renderToStaticMarkup(<MethodCard method={testMethod} canEdit selected onChange={() => {}} onReset={() => {}}/>);
    for (const text of ["Публикувано предписание", "90–92% HRmax", "Адаптация за onFlows", "Равномерен ритъм", "Спри при загуба", "2 серии", "0:04:00–0:08:00", "активна, зона 1", "Горните граници", "href=\"https://doi.org/example\""]) expect(html).toContain(text);
    expect(methodPaceText([15, 15.3])).toBe("3:55–4:00 мин/km");
  });
  it("explains unavailable CV without presenting stale speed as a target", () => {
    const method = { ...testMethod, resolution: { ...testMethod.resolution!, status: "UNAVAILABLE", reason: "CV_REQUIRES_ACCEPTED_SHORT_AND_LONG_TESTS" } };
    const html = renderToStaticMarkup(<MethodCard method={method} canEdit={false} selected onChange={() => {}} onReset={() => {}}/>);
    expect(html).toContain("приети максимални тестове");
    expect(html).toContain("Няма приложима оценка");
    expect(html).not.toContain("CV_REQUIRES");
    expect(html).not.toContain("17,6–18,4");
  });
  it("preserves method preferences when the main planning profile is serialized", () => {
    const preferences = { enabled: true, overrides: { XC01: { speed_multiplier: [.98, 1.02] as [number, number], enabled: false } } };
    const profile = parseManagementProfile({ ...defaultManagementProfile("2026-10-12"), discipline: "10 km", method_library: preferences });
    expect(JSON.parse(JSON.stringify(profile)).method_library).toEqual(preferences);
    for (const bad of [{ speed_multiplier: [1.1, .9] }, { anchor: "V5" }, { repetitions: [1.5, 4] }]) expect(() => parseMethodPreferences({ enabled: true, overrides: { XC01: bad } })).toThrow();
  });
  it("links every component of a combined saved session once", () => {
    const metadata = { id: testMethod.id, title: testMethod.title, guidance: testMethod.guidance, resolution: testMethod.resolution };
    const session = { method_library: metadata, blocks: [{ method_library: metadata }, { method_library: { ...metadata, id: "XC02" } }] } as DraftSession;
    expect(sessionMethods(session).map(m => m.id)).toEqual(["XC01", "XC02"]);
    const html = renderToStaticMarkup(<SessionMethodGuidance session={session}/>);
    expect(html).toContain('href="/methods?method=XC01"');
    expect(html).toContain('href="/methods?method=XC02"');
    expect(html).toContain("Запази ритъма");
    expect(html).toContain("запазени при съставянето");
    expect(renderToStaticMarkup(<SessionMethodGuidance session={{ blocks: [] } as unknown as DraftSession}/>)).toBe("");
  });
});
