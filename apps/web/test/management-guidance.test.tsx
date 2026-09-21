import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { managementGuidance, hasProgramDays } from "../lib/management-guidance";
import { TrainingManagement } from "../components/training-management";
import { ManagementProfileEditor } from "../components/management-profile-editor";
import { TrainingPlanOverview } from "../components/training-plan-overview";
import { defaultManagementProfile, type DraftRecord, type PlanningDraft } from "../lib/training-management";
import type { SyncState } from "../lib/sync";
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
const today = "2026-09-21";
const base = { configured: true, dirty: false, today };
const draft = (codes: string[], eligible = false, stale: boolean | null = false): DraftRecord => ({ entry_key: "test", revision: 1, stale, payload: { schema_version: "planning-draft-v1", engine_version: "v2", status: "BLOCKED", days: [], start_date: today, end_date: "2026-09-27", source: { generation_id: "old" }, warnings: codes.map(code => ({ code, message: code })), activation_eligible: eligible } });
const sync = (generation: string, date: string): SyncState => ({ schema_version: "sync-state-v1", job_id: null, scope: "FULL", state: "SUCCEEDED", stage: null, progress_percent: 100, requested_at: null, started_at: null, finished_at: null, retry_at: null, failure_code: null, active_generation_id: generation, active_revision: 2, analysis_as_of: date, activated_at: null });

describe("one next action without relaxing publication gates", () => {
  it("directs stale history to import even if optional methods are unconfigured", () => {
    expect(managementGuidance({ ...base, draft: draft(["METHOD_PROFILE_Z4", "METHOD_PROFILE_STR", "STALE_LOAD_SNAPSHOT"]) }).step).toBe("SYNC");
    expect(managementGuidance({ ...base, draft: draft(["METHOD_PROFILE_Z4", "METHOD_PROFILE_STR"], true) }).step).toBe("REVIEW");
    expect(managementGuidance({ ...base, draft: draft([], false) }).step).toBe("CHECK");
  });
  it("refreshes old drafts after import, but does not treat a newer stale snapshot as current", () => {
    expect(managementGuidance({ ...base, draft: draft(["STALE_LOAD_SNAPSHOT"]), sync: sync("new", today) }).step).toBe("GENERATE");
    expect(managementGuidance({ ...base, draft: draft([]), sync: sync("new", "2026-09-19") }).step).toBe("SYNC");
    expect(managementGuidance({ ...base, draft: draft([], true, null) }).step).toBe("GENERATE");
    expect(managementGuidance({ ...base, draft: draft(["WEEKLY_VOLUME_REQUIRED"], false, true) }).step).toBe("GENERATE");
  });
  it("keeps profile work on its own page and hides blocked empty weeks", () => {
    const blocked = draft(["STALE_LOAD_SNAPSHOT", "METHOD_PROFILE_Z4"]);
    expect(hasProgramDays(blocked.payload)).toBe(false);
    const html = renderToStaticMarkup(<TrainingManagement athleteName="Test" canEdit initialProfile={{ configured: true, revision: 1, profile: { ...defaultManagementProfile(today), discipline: "5 km" } }} initialDrafts={[blocked]} today={today} />);
    expect(html).toContain("Първо обнови тренировките");
    expect(html).toContain('value="/management"');
    expect(html).not.toContain("Започни програмата");
    expect(html).not.toContain("management-week-list");
    expect(html).not.toContain("Основен спорт");
    expect(html).toContain('<details class="management-panel" id="plan-details">');
  });
  it("starts unconfigured profiles with required sport and goal, leaving calendar optional", () => {
    const html = renderToStaticMarkup(<ManagementProfileEditor initialProfile={{ configured: false, profile: null, revision: 0 }} today={today} />);
    expect(html).toContain("Дисциплина · задължително");
    expect(html).toContain("Възраст, години · по желание");
    expect(html).toContain("2. Дни и обем");
    expect(html).not.toContain("Изграждаща доза");
    expect(html).not.toContain("Започни програмата");
  });
  it("does not convert missing actuals or missing outlook targets into zero", () => {
    const plan = draft([]).payload as PlanningDraft;
    plan.long_term = { schema_version: "training-outlook-v1", weeks: [{ start_date: today, end_date: "2026-09-27", phases: ["GENERAL_PREPARATION"], accents: ["Z2"], components: { Z2: { target_weekly_effective: null, target_index_7_40: null } } }] };
    const html = renderToStaticMarkup(<TrainingPlanOverview plan={plan} today={today} outcomes={[{ date: today, status: "UNKNOWN", planned_title: "Test", planned_minutes: 60, actual_minutes: null }]} />);
    expect(html).toContain("Непълни данни");
    expect(html).toContain("<td>—</td>");
    expect(html).toContain("STR е отделен компонент");
    expect(html).not.toContain("NaN");
  });
});
