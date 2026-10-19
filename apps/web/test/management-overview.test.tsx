// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TrainingManagement } from "../components/training-management";
import { microcycleVolume } from "../lib/microcycle-volume";
import { parseManagementOverviewActive, parseManagementView, type ManagementOverviewActive } from "../lib/management-overview";
import { COMPONENTS, daySessions, defaultManagementProfile, parseDraftRecord, type ActivePlanRecord,
  type ActivePlanResponse, type ManagementOutlook, type PlanningDraft } from "../lib/training-management";

const today = "2026-09-21";
const engine = "training-management-v49";
const profile = { configured: true, revision: 8, today, profile: { ...defaultManagementProfile(today), discipline: "5000 m" } };
const vector = Object.fromEntries(COMPONENTS.map(zone => [zone, 95]));
const session = (zone: string, minutes: number) => ({
  method_id: `METHOD-${zone}`, title: "Тестова задача", sport: "Run", zone, purpose: "BUILDING",
  main_work_minutes: minutes, total_minutes: minutes + 15,
  canonical_effective_load: vector, direct_equivalent_minutes: vector,
  blocks: [
    { kind: "WARMUP", label: "Загрявка", zone: "Z1", duration_min: 10, target_hr_bpm: null, target_speed_kmh: null, repetition: null, instructions: "Леко движение." },
    { kind: "WORK", label: "Основна работа", zone, duration_min: minutes, target_hr_bpm: null, target_speed_kmh: null, repetition: null, instructions: "Запазете усилието." },
    { kind: "REST", label: "Пауза", zone: "NONE", duration_min: 5, target_hr_bpm: null, target_speed_kmh: null, repetition: null, instructions: "Почивка." },
  ],
  dose_evidence: { explanation: "Индивидуална доза.", limits: [], fallback_reasons: [] },
});
function fullPlan(minutes: number): PlanningDraft {
  const first = session("Z3", minutes), second = session("STR", 15);
  return parseDraftRecord({ entry_key: today, revision: 4, stale: false, payload: {
    schema_version: "planning-draft-v1", engine_version: engine, status: "DRAFT", start_date: today, end_date: "2026-09-27",
    source: {}, warnings: [], history_comparison: [{ start_date: "2026-08-17", end_date: "2026-08-23", actual_minutes: 200, covered_days: 7 }],
    days: ["TRAINING", "REST", "UNAVAILABLE", "SKIPPED", "REVIEW_REQUIRED", "EXISTING_ACTIVITY", "TRAINING"].map((status, i) => ({
      date: `2026-09-${21 + i}`, status, period: "GENERAL_PREPARATION", taper: false, explanation: "Дневна задача.",
      readiness_before: vector, readiness_after: vector, rejected_alternatives: [],
      load_budget: { remaining_weekly_minutes: null, components: {} },
      session: i === 0 ? first : null, sessions: i === 0 ? [first, second] : [],
    })),
  } }).payload;
}
const outlook: ManagementOutlook = {
  schema_version: "training-outlook-preview-v1", engine_version: engine, profile_revision: 8, generated_at: `${today}T10:00:00Z`,
  volume_context: {}, periodization: { phases: [] },
  history_comparison: [{ start_date: "2026-08-24", end_date: "2026-08-30", actual_minutes: 100, covered_days: 7 }],
  long_term: { schema_version: "training-outlook-v1", weeks: [{ start_date: today, end_date: "2026-09-27", accents: ["Z3"], phases: ["GENERAL_PREPARATION"],
    volume_budget_minutes: 420, components: { Z3: { target_period_q: 75 } } }] },
};
function activeResponse(status: ActivePlanRecord["payload"]["status"], hasProposal = false, stale = false): ActivePlanResponse {
  return { history: [], active: { revision: 9, stale, actionable: !stale, payload: {
    schema_version: "active-plan-v2", status, mode: "REVIEW", reason: "Проверка.", plan: fullPlan(20), proposal: hasProposal ? fullPlan(40) : null,
    changes: [], decisions: {}, outcomes: [{ date: today, status: "EXECUTED", planned_title: "Тестова задача", planned_minutes: 60, actual_minutes: 75 }],
  } } };
}
function compactActive(active: ActivePlanResponse): ManagementOverviewActive {
  if (!active.active) return parseManagementOverviewActive({ revision: null, stale: false, status: null, has_proposal: false, engine_version: null, days: [], history_comparison: [], outcomes: [] });
  const row = active.active, plan = row.payload.proposal ?? row.payload.plan;
  return parseManagementOverviewActive({ revision: row.revision, stale: row.stale, status: row.payload.status, has_proposal: row.payload.proposal !== null,
    engine_version: plan.engine_version, history_comparison: plan.history_comparison,
    days: plan.days.map(day => ({ date: day.date, status: day.status, sessions: daySessions(day).map(s => ({ total_minutes: s.total_minutes,
      blocks: s.blocks.map(b => ({ zone: b.zone, duration_min: b.duration_min })) })) })),
    outcomes: row.payload.outcomes.map(o => ({ date: o.date, planned_minutes: o.planned_minutes, actual_minutes: o.actual_minutes })),
  });
}
const props = { athleteName: "Спортист", canEdit: true, initialView: "overview" as const, initialProfile: profile, initialDrafts: [], initialOutlook: outlook, today };

beforeEach(() => vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true));
afterEach(() => vi.unstubAllGlobals());

async function sessionMarkup(initialActive: ActivePlanResponse, initialOverviewActive?: ManagementOverviewActive, currentOutlook = outlook) {
  const box = document.createElement("div"), root = createRoot(box);
  try {
    await act(async () => root.render(<TrainingManagement {...props} initialOutlook={currentOutlook} initialActive={initialActive} initialOverviewActive={initialOverviewActive} />));
    await act(async () => [...box.querySelectorAll("button")].find(b => b.textContent === "Продължителност на съставените сесии")!.click());
    return box.innerHTML;
  } finally { await act(async () => root.unmount()); }
}

describe("compact management overview contract", () => {
  it("parses only the compact read model without pretending it is an executable plan", () => {
    const compact = compactActive(activeResponse("REVIEW_REQUIRED", true));
    const parsed = parseManagementView({ schema_version: "management-overview-v1", profile, overview_active: compact,
      drafts: { drafts: [] }, outlook: { configured: true, outlook }, sync: null });
    expect(parsed.overviewActive).toEqual(compact);
    expect(parsed.active).toEqual({ active: null, history: [] });
    expect(parsed.drafts).toEqual([]);
    expect(parsed.outlook).toEqual(outlook);
    expect(parsed.overviewActive).not.toHaveProperty("schema_version", "planning-draft-v1");
  });

  it("accepts the original API response when the projection query is ignored", () => {
    const active = activeResponse("ACTIVE");
    const parsed = parseManagementView({ profile, active, drafts: { drafts: [], current_input_fingerprint: "current" }, outlook: { configured: true, outlook }, sync: null });
    expect(parsed.overviewActive).toBeUndefined();
    expect(parsed.active).toEqual(active);
    expect(parsed.inputFingerprint).toBe("current");
  });

  it.each([
    { revision: 0 }, { stale: null }, { status: "DRAFT" }, { has_proposal: "false" }, { engine_version: 49 },
    { days: [{ date: "2026-02-30", status: "REST", sessions: [] }] },
    { days: [{ date: today, status: "TRAINING", sessions: [{ total_minutes: -1, blocks: [] }] }] },
    { days: [{ date: today, status: "TRAINING", sessions: [{ total_minutes: 1, blocks: [{ zone: "Z3", duration_min: Infinity }] }] }] },
    { days: [{ date: today, status: "TRAINING", session: null }] },
    { outcomes: [{ date: today, planned_minutes: undefined, actual_minutes: null }] },
    { outcomes: [{ date: today, planned_minutes: null, actual_minutes: -1 }] },
  ])("rejects malformed compact data without using full-plan fallback: %j", change => {
    const overview_active = { ...compactActive(activeResponse("ACTIVE")), ...change };
    expect(() => parseManagementView({ schema_version: "management-overview-v1", profile, overview_active, active: activeResponse("ACTIVE"), drafts: { drafts: [] } })).toThrow();
  });

  it("rejects contradictory empty-active metadata and unknown schema versions", () => {
    const empty = compactActive({ active: null, history: [] });
    expect(() => parseManagementOverviewActive({ ...empty, has_proposal: true })).toThrow();
    expect(() => parseManagementOverviewActive({ ...empty, outcomes: [{ date: today, planned_minutes: null, actual_minutes: null }] })).toThrow();
    expect(() => parseManagementView({ schema_version: "management-overview-v2" })).toThrow();
    expect(parseManagementOverviewActive({ ...compactActive(activeResponse("ACTIVE")), outcomes: [{ date: today, planned_minutes: null, actual_minutes: null }] }).outcomes[0]).toEqual({ date: today, planned_minutes: null, actual_minutes: null });
  });

  it("preserves order, both sessions, unknown zones and the block-total validity guard", () => {
    const full = activeResponse("ACTIVE");
    // Legacy day.session remains supported by the full read model.
    delete full.active!.payload.plan.days[0].sessions;
    const compact = compactActive(full);
    expect(compact.days.map(d => d.date)).toEqual(full.active!.payload.plan.days.map(d => d.date));
    expect(compact.days[0].sessions![0].blocks.map(b => b.zone)).toEqual(["Z1", "Z3", "NONE"]);
    const volume = microcycleVolume(compact, today, "2026-09-27");
    expect(volume).toEqual(microcycleVolume(full.active!.payload.plan, today, "2026-09-27"));
    expect(volume.total).toBe(35);
    expect(volume.unallocated).toBe(5);
    const bad = structuredClone(compact);
    bad.days[0].sessions![0].total_minutes = 1;
    expect(microcycleVolume(bad, today, today).available).toBe(false);
  });
});

describe("full and compact overview rendering parity", () => {
  it.each([
    ["ACTIVE", false, false], ["REVIEW_REQUIRED", true, false], ["PAUSED", false, false], ["COMPLETED", false, false],
    ["ACTIVE", false, true], ["REVIEW_REQUIRED", true, true],
  ] as const)("preserves %s / proposal=%s / stale=%s", async (status, proposal, stale) => {
    const active = activeResponse(status, proposal, stale), compact = compactActive(active);
    const fullStatic = renderToStaticMarkup(<TrainingManagement {...props} initialActive={active} />);
    const compactStatic = renderToStaticMarkup(<TrainingManagement {...props} initialOverviewActive={compact} />);
    expect(compactStatic).toBe(fullStatic);
    const full = await sessionMarkup(active), projected = await sessionMarkup({ active: null, history: [] }, compact);
    expect(projected).toBe(full);
    expect(projected).toContain("1:15:00"); // Outcomes are retained even when prescribed time is stale.
    expect(projected).toContain(stale ? "2026-08-24" : "2026-08-17");
    expect(projected).not.toContain(stale ? "2026-08-17" : "2026-08-24");
    if (stale) expect(projected).toContain("Няма актуални съставени сесии");
    else if (proposal) expect(projected).toContain("предложена адаптация за преглед");
    else if (status === "ACTIVE") expect(projected).toContain("текущата утвърдена програма");
    else expect(projected).toContain("запазена програма, която в момента не е активна");
  });

  it("hides prescribed time for an engine mismatch while retaining outcomes and live history", async () => {
    const active = activeResponse("ACTIVE"), compact = compactActive(active), changed = { ...outlook, engine_version: "new-engine" };
    const full = await sessionMarkup(active, undefined, changed);
    const projected = await sessionMarkup({ active: null, history: [] }, compact, changed);
    expect(projected).toBe(full);
    expect(projected).toContain("Няма актуални съставени сесии");
    expect(projected).toContain("2026-08-24");
    expect(projected).not.toContain("2026-08-17");
    expect(projected).toContain("1:15:00");
  });

  it("preserves the no-active state and keeps compact inputs out of the weekly interface", async () => {
    const empty: ActivePlanResponse = { active: null, history: [] }, compact = compactActive(empty);
    expect(await sessionMarkup(empty, compact)).toBe(await sessionMarkup(empty));
    const active = activeResponse("ACTIVE");
    const weeklyProps = { ...props, initialView: "week" as const, initialActive: active };
    expect(renderToStaticMarkup(<TrainingManagement {...weeklyProps} initialOverviewActive={compactActive(activeResponse("PAUSED", true))} />))
      .toBe(renderToStaticMarkup(<TrainingManagement {...weeklyProps} />));
  });
});
