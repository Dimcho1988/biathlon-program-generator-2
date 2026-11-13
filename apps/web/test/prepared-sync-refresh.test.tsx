// @vitest-environment jsdom
import {act} from "react";
import {createRoot, type Root} from "react-dom/client";
import {afterEach, beforeEach, expect, it, vi} from "vitest";
import {PreparedViewRetry} from "../components/prepared-view-retry";
import {SyncStatusPanel} from "../components/sync-status-panel";
import type {SyncState} from "../lib/sync";

const {refresh, router} = vi.hoisted(() => {const refresh=vi.fn(); return {refresh,router:{refresh}};});
vi.mock("next/navigation", () => ({useRouter: () => router}));
const completed: SyncState = {
  schema_version:"sync-state-v1",job_id:"sync-job",scope:"FULL",state:"SUCCEEDED",stage:"MODELS",
  progress_percent:100,requested_at:"2026-11-13T07:00:00Z",started_at:"2026-11-13T07:00:01Z",
  finished_at:"2026-11-13T07:01:00Z",retry_at:null,failure_code:null,
  active_generation_id:"new-generation",active_revision:2,analysis_as_of:"2026-11-13",activated_at:"2026-11-13T07:01:00Z",
};
let root: Root, container: HTMLDivElement;
beforeEach(() => {
  vi.useFakeTimers();vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT",true);refresh.mockClear();
  container=document.createElement("div");document.body.append(container);root=createRoot(container);
});
afterEach(async () => {
  await act(async()=>root.unmount());container.remove();vi.clearAllTimers();vi.useRealTimers();vi.unstubAllGlobals();
});

it("lets prepared-page backoff own refresh after publication while the old page is updating", async () => {
  for (let n=0;n<3;n++) await act(async()=>root.render(<>
    <SyncStatusPanel initialState={{...completed}} renderedGenerationId="old-generation" deferViewRefresh/>
    <PreparedViewRetry/>
  </>));
  expect(refresh).not.toHaveBeenCalled();
  await act(async()=>vi.advanceTimersByTimeAsync(120_000));
  expect(refresh).toHaveBeenCalledTimes(7);
});

it("keeps reading queue progress without triggering an extra page refresh", async () => {
  const request=vi.fn(async()=>Response.json(completed));vi.stubGlobal("fetch",request);
  await act(async()=>root.render(<>
    <SyncStatusPanel initialState={{...completed,state:"RUNNING",progress_percent:50,finished_at:null}}
      renderedGenerationId="old-generation" deferViewRefresh/>
    <PreparedViewRetry/>
  </>));
  await act(async()=>vi.advanceTimersByTimeAsync(2000));
  expect(request).toHaveBeenCalledTimes(1);
  expect(refresh).not.toHaveBeenCalled();
  expect(container.textContent).toContain("Новата версия е активирана");
  await act(async()=>vi.advanceTimersByTimeAsync(3000));
  expect(refresh).toHaveBeenCalledTimes(1);
});

it("still refreshes a normal page immediately when its generation changes", async () => {
  await act(async()=>root.render(<SyncStatusPanel initialState={completed} renderedGenerationId="old-generation"/>));
  expect(refresh).toHaveBeenCalledTimes(1);
});

it("does not repeatedly refresh a legacy READY page whose cached sync was unavailable", async () => {
  for (let n=0;n<3;n++) await act(async()=>root.render(<SyncStatusPanel
    initialState={{...completed}} renderedGenerationId={null} renderedGenerationKnown={false}/>));
  await act(async()=>vi.advanceTimersByTimeAsync(120_000));
  expect(refresh).not.toHaveBeenCalled();
});

it("still polls a running job with unknown rendered source and refreshes once on success", async () => {
  const running={...completed,state:"RUNNING" as const,progress_percent:50,finished_at:null};
  const request=vi.fn().mockResolvedValueOnce(Response.json(running)).mockResolvedValueOnce(Response.json(completed));
  vi.stubGlobal("fetch",request);
  await act(async()=>root.render(<SyncStatusPanel
    initialState={running} renderedGenerationId={null} renderedGenerationKnown={false}/>));
  await act(async()=>vi.advanceTimersByTimeAsync(2000));
  expect(request).toHaveBeenCalledTimes(1);
  expect(refresh).not.toHaveBeenCalled();
  await act(async()=>vi.advanceTimersByTimeAsync(3000));
  expect(request).toHaveBeenCalledTimes(2);
  expect(refresh).toHaveBeenCalledTimes(1);
  await act(async()=>root.render(<SyncStatusPanel
    initialState={completed} renderedGenerationId={null} renderedGenerationKnown={false}/>));
  await act(async()=>vi.advanceTimersByTimeAsync(120_000));
  expect(refresh).toHaveBeenCalledTimes(1);
});
