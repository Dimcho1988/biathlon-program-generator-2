import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { SyncActionForm } from "../components/sync-action-form";
import { SyncStatusPanel } from "../components/sync-status-panel";
import { parseDashboardView } from "../lib/dashboard-view";
import { completedWorkFixture, loadHistoryFixture, recoveryHistoryFixture, trainingStatusFixture, volumeHistoryFixture } from "../lib/fixture";
import { parseSyncEnqueueResponse, parseSyncState, syncInProgress, syncPollDelay, syncRequiresViewRefresh, type SyncState } from "../lib/sync";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));

const runningState: SyncState = {
  schema_version: "sync-state-v1",
  job_id: "sync-job-1",
  scope: "FULL",
  state: "RUNNING",
  stage: "MODELS",
  progress_percent: 45,
  requested_at: "2026-08-27T06:00:00Z",
  started_at: "2026-08-27T06:00:01Z",
  finished_at: null,
  retry_at: null,
  failure_code: null,
  active_generation_id: "gen-41",
  active_revision: 41,
  analysis_as_of: "2026-08-26",
  activated_at: "2026-08-26T18:00:00Z",
};

describe("sync contracts", () => {
  it("strictly parses enqueue and rejects extra or terminal fields", () => {
    const enqueue = {
      schema_version: "sync-enqueue-v1",
      job_id: "sync-job-1",
      scope: "FULL",
      state: "QUEUED",
      coalesced: false,
    };
    expect(parseSyncEnqueueResponse(enqueue)).toEqual(enqueue);
    expect(() => parseSyncEnqueueResponse({ ...enqueue, unexpected: true })).toThrow(/enqueue/);
    expect(() => parseSyncEnqueueResponse({ ...enqueue, state: "SUCCEEDED" })).toThrow(/enqueue/);
  });

  it("accepts every public lifecycle state and enforces coherent identity", () => {
    for (const state of ["QUEUED", "RUNNING", "RETRY_WAIT", "SUCCEEDED", "FAILED", "SUPERSEDED"] as const)
      expect(parseSyncState({ ...runningState, state }).state).toBe(state);
    expect(syncInProgress(parseSyncState(runningState))).toBe(true);
    expect(() => parseSyncState({ ...runningState, active_revision: 0 })).toThrow(/активна версия/);
    expect(() => parseSyncState({ ...runningState, state: "IDLE" })).toThrow(/несъгласуван sync/);
    expect(() => parseSyncState({ ...runningState, unexpected: true })).toThrow(/sync статус/);
  });

  it("accepts an idle profile both before and after its first active generation", () => {
    const idle = {
      ...runningState,
      job_id: null,
      scope: null,
      state: "IDLE" as const,
      stage: null,
      progress_percent: 0,
      requested_at: null,
      started_at: null,
    };
    expect(parseSyncState(idle).active_revision).toBe(41);
    expect(parseSyncState({
      ...idle,
      active_generation_id: null,
      active_revision: 0,
      analysis_as_of: null,
      activated_at: null,
    }).active_generation_id).toBeNull();
  });

  it("uses a bounded polling cadence", () => {
    expect([0, 1, 2, 3, 4, 100].map(syncPollDelay)).toEqual([2_000, 3_000, 5_000, 8_000, 10_000, 10_000]);
  });

  it("refreshes a stale view even when the job was already terminal at first render", () => {
    const succeeded = { ...runningState, state: "SUCCEEDED" as const, active_generation_id: "gen-42", active_revision: 42 };
    expect(syncRequiresViewRefresh(succeeded, "gen-41")).toBe(true);
    expect(syncRequiresViewRefresh(succeeded, null)).toBe(true);
    expect(syncRequiresViewRefresh(succeeded, "gen-42")).toBe(false);
  });
});

describe("coherent dashboard generation", () => {
  it("parses all dashboard resources under one generation", () => {
    const view = {
      schema_version: "dashboard-view-v1",
      generation_id: "gen-41",
      revision: 41,
      analysis_as_of: trainingStatusFixture.as_of,
      activated_at: "2026-08-26T18:00:00Z",
      training_status: trainingStatusFixture,
      completed_work: completedWorkFixture,
      load_history: loadHistoryFixture,
      recovery_history: recoveryHistoryFixture,
      volume_history: volumeHistoryFixture,
    };
    expect(parseDashboardView(view)).toEqual(view);
    expect(() => parseDashboardView({ ...view, revision: 0 })).toThrow(/несъгласувана версия/);
    expect(() => parseDashboardView({ ...view, unexpected: true })).toThrow(/dashboard view/);
  });

  it("accepts an empty view only when there is no active generation", () => {
    const empty = {
      schema_version: "dashboard-view-v1",
      generation_id: null,
      revision: 0,
      analysis_as_of: null,
      activated_at: null,
      training_status: null,
      completed_work: null,
      load_history: null,
      recovery_history: null,
      volume_history: null,
    };
    expect(parseDashboardView(empty)).toEqual(empty);
    expect(() => parseDashboardView({ ...empty, training_status: trainingStatusFixture })).toThrow(/задължителния тренировъчен анализ/);
  });

  it("keeps a rollout-compatible legacy aggregate visible before its first generation head", () => {
    const legacy = {
      schema_version: "dashboard-view-v1",
      generation_id: null,
      revision: 0,
      analysis_as_of: trainingStatusFixture.as_of,
      activated_at: "2026-08-26T18:00:00Z",
      training_status: trainingStatusFixture,
      completed_work: completedWorkFixture,
      load_history: loadHistoryFixture,
      recovery_history: null,
      volume_history: volumeHistoryFixture,
    };
    const parsed = parseDashboardView(legacy);
    expect(parsed.training_status).toEqual(trainingStatusFixture);
    expect(parsed.recovery_history).toBeNull();
  });
});

describe("sync UI primitives", () => {
  it("shows the last active revision while a background job runs", () => {
    const html = renderToStaticMarkup(<SyncStatusPanel initialState={runningState} renderedGenerationId="gen-41" />);
    expect(html).toContain("Обновяваме тренировъчните данни");
    expect(html).toContain("последната валидна версия 41");
    expect(html).toContain("value=\"45\"");
  });

  it("offers explicit retry after failure and disables duplicate actions while busy", () => {
    const failed = { ...runningState, state: "FAILED" as const, finished_at: "2026-08-27T06:05:00Z", failure_code: "PROVIDER_UNAVAILABLE" };
    const failedHtml = renderToStaticMarkup(<SyncStatusPanel initialState={failed} renderedGenerationId="gen-41" />);
    expect(failedHtml).toContain("Последната валидна версия остава активна");
    expect(failedHtml).toContain("Опитай отново");
    const busyHtml = renderToStaticMarkup(<SyncActionForm busy />);
    expect(busyHtml).toContain("disabled");
    expect(busyHtml).toContain("Обновяването е в ход");
  });

  it("requires a full sync when legacy Recovery lacks persisted Tref provenance", () => {
    const failed = {
      ...runningState,
      scope: "RECOVERY" as const,
      state: "FAILED" as const,
      finished_at: "2026-08-27T06:05:00Z",
      failure_code: "RECOVERY_SOURCE_REFRESH_REQUIRED",
    };
    const html = renderToStaticMarkup(<SyncStatusPanel initialState={failed} renderedGenerationId="gen-41" />);
    expect(html).toContain("Необходимо е пълно обновяване");
    expect(html).toContain("не съдържа необходимите Tref данни");
    expect(html).toContain('name="scope" value="FULL"');
    expect(html).not.toContain('name="scope" value="RECOVERY"');
  });
});
