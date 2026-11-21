// @vitest-environment jsdom
import {act} from "react";
import {createRoot, type Root} from "react-dom/client";
import {beforeEach, afterEach, expect, it, vi} from "vitest";
import {ManagementPreparationRetry} from "../components/management-preparation-retry";
const {refresh, router, read} = vi.hoisted(() => {const refresh=vi.fn(); return {refresh,router:{refresh},read:vi.fn()};});
vi.mock("next/navigation",()=>({useRouter:()=>router}));
vi.mock("../components/management-scope",()=>({useManagementFetch:()=>read}));
let root: Root, container: HTMLDivElement;
beforeEach(()=>{vi.useFakeTimers();vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT",true);vi.clearAllMocks();
 container=document.createElement("div");document.body.append(container);root=createRoot(container);
 read.mockResolvedValue({ok:true,status:200,json:async()=>({ready:false})});});
afterEach(async()=>{await act(async()=>root.unmount());container.remove();vi.useRealTimers();vi.restoreAllMocks();vi.unstubAllGlobals();});
it("polls only status beyond two minutes then refreshes once",async()=>{
 await act(async()=>root.render(<ManagementPreparationRetry view="overview"/>));
 await act(async()=>vi.advanceTimersByTimeAsync(180_000));
 expect(read.mock.calls.length).toBeGreaterThan(7);expect(refresh).not.toHaveBeenCalled();
 expect(read.mock.calls[0][0]).toContain("view=overview");
 read.mockResolvedValue({ok:true,status:200,json:async()=>({ready:true})});
 await act(async()=>vi.advanceTimersByTimeAsync(60_000));expect(refresh).toHaveBeenCalledTimes(1);
});
it.each([401,403,412])("stops on access failure %s",async(status)=>{
 read.mockResolvedValue({ok:false,status});await act(async()=>root.render(<ManagementPreparationRetry/>));
 await act(async()=>vi.advanceTimersByTimeAsync(180_000));expect(read).toHaveBeenCalledTimes(1);expect(refresh).not.toHaveBeenCalled();
});
it("skips hidden pages and retries connection errors",async()=>{
 const visibility=vi.spyOn(document,"visibilityState","get").mockReturnValue("hidden");
 await act(async()=>root.render(<ManagementPreparationRetry/>));await act(async()=>vi.advanceTimersByTimeAsync(30_000));expect(read).not.toHaveBeenCalled();
 visibility.mockReturnValue("visible");read.mockRejectedValue(new Error("offline"));
 await act(async()=>vi.advanceTimersByTimeAsync(30_000));expect(container.textContent).toContain("Връзката временно");expect(refresh).not.toHaveBeenCalled();
});
it("bounds waiting and cancels a late result on unmount",async()=>{
 await act(async()=>root.render(<ManagementPreparationRetry/>));await act(async()=>vi.advanceTimersByTimeAsync(16*60_000));
 const count=read.mock.calls.length;await act(async()=>vi.advanceTimersByTimeAsync(60_000));expect(read).toHaveBeenCalledTimes(count);
 expect(container.textContent).toContain("Подготовката продължава");
 let finish!: (value:unknown)=>void;read.mockImplementation(()=>new Promise(resolve=>{finish=resolve;}));
 await act(async()=>container.querySelector("button")!.click());await act(async()=>vi.advanceTimersByTimeAsync(5000));
 await act(async()=>root.render(null));await act(async()=>finish({ok:true,status:200,json:async()=>({ready:true})}));expect(refresh).toHaveBeenCalledTimes(1);
});
