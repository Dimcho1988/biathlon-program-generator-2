import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { SessionMethodGuidance } from "../components/session-method-guidance";
import { SessionRole } from "../components/session-role";
import { ManagementRules } from "../components/management-rules";
import { defaultManagementProfile, parseManagementProfile, type DraftSession } from "../lib/training-management";

describe("sport strength prescriptions", () => {
  it("shows the complete frozen bank, repetitions, actual percentage and reserve", () => {
    const session = { purpose: "ACTIVATION", blocks: [], dose_evidence: { resolved_role: "ACTIVATION", strength_prescription: {
      exercise_bank_id: "Run-capacity-v1", main_sport: "Run", method: "POWER", total_sets: 9, building_total_sets: 16, dose_fraction: .5625,
      exercises: [{ id: "squat", name: "Полуклек", sets: 3, reps: 4, muscle_groups: "Квадрицепс" },
        { id: "hinge", name: "Румънска тяга", sets: 2, reps: 4, muscle_groups: "Задна бедрена група" }],
      reserve_definition: "Допълнителни повторения със същата тежест", continuity: "Запази усвоените упражнения", progression: "Записвай тежестта и резерва",
      sources: [{authors: "Taipale et al. (2010)", url: "https://pubmed.ncbi.nlm.nih.gov/20432192/", protocol: "Original protocol"}],
    } } } as unknown as DraftSession;
    const html = renderToStaticMarkup(<><SessionRole session={session} day={{period: "PRECOMPETITION"}}/><SessionMethodGuidance session={session}/></>);
    for (const text of ["Активираща", "56,3%", "3 × 4", "2 × 4", "Полуклек", "Румънска тяга", "същата тежест", "Треньорска адаптация", "20432192"]) expect(html).toContain(text);
  });

  it("persists the per-session fraction and rejects double scaling values", () => {
    const profile = {...defaultManagementProfile("2026-10-16"), discipline: "10 km", strength_enabled: true};
    expect(parseManagementProfile(profile).strength_maintenance_fraction).toBe(.6);
    expect(parseManagementProfile({...profile, strength_maintenance_fraction: .55}).strength_maintenance_fraction).toBe(.55);
    for (const invalid of [.25, .36, .61, null]) expect(() => parseManagementProfile({...profile, strength_maintenance_fraction: invalid})).toThrow();
  });

  it("uses the main sport even when the current means is running", () => {
    const profile = {...defaultManagementProfile("2026-10-16"), sport: "NordicSki" as const, actual_sport: "Run" as const, strength_enabled: true};
    const html = renderToStaticMarkup(<ManagementRules profile={profile} onChange={() => {}} today="2026-10-16"/>);
    expect(html).toContain("едновременно оттласкване на скрипец");
    expect(html).toContain("за едно занимание");
    expect(html).not.toContain("Максимум кръгове");
  });
});
