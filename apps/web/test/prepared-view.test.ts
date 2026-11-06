import {afterEach,expect,it,vi} from "vitest";
import {fetchPrepared} from "../lib/prepared-view";

afterEach(()=>vi.useRealTimers());
const pending=()=>new Response(null,{status:503,headers:{"X-OnFlows-Preparation":"pending"}});

it("polls only an explicitly pending read and returns the prepared result",async()=>{
  vi.useFakeTimers();
  const request=vi.fn<typeof fetch>().mockResolvedValueOnce(pending()).mockResolvedValueOnce(Response.json({ready:true}));
  const result=fetchPrepared("/prepared",{},request);
  await vi.advanceTimersByTimeAsync(1500);
  expect(await (await result).json()).toEqual({ready:true});
  expect(request).toHaveBeenCalledTimes(2);
});
it("never replays a write, including a pending response",async()=>{
  const response=pending(),request=vi.fn<typeof fetch>().mockResolvedValue(response);
  expect(await fetchPrepared("/action",{method:"POST"},request)).toBe(response);
  expect(request).toHaveBeenCalledTimes(1);
});
it("does not hide an unrelated service error with polling",async()=>{
  const response=new Response(null,{status:503}),request=vi.fn<typeof fetch>().mockResolvedValue(response);
  expect(await fetchPrepared("/read",{},request)).toBe(response);
  expect(request).toHaveBeenCalledTimes(1);
});
it("cancels a pending read when its component is closed",async()=>{
  vi.useFakeTimers();
  const controller=new AbortController(),request=vi.fn<typeof fetch>().mockImplementation(async()=>pending());
  const result=fetchPrepared("/read",{signal:controller.signal},request);
  const rejected=expect(result).rejects.toMatchObject({name:"AbortError"});
  await vi.advanceTimersByTimeAsync(1);controller.abort();
  await rejected;
  await vi.advanceTimersByTimeAsync(30000);
  expect(request).toHaveBeenCalledTimes(1);
});
it("bounds preparation polling and exposes the still-pending response",async()=>{
  vi.useFakeTimers();
  const request=vi.fn<typeof fetch>().mockImplementation(async()=>pending());
  const result=fetchPrepared("/read",{},request);
  await vi.advanceTimersByTimeAsync(130000);
  expect((await result).status).toBe(503);
  expect(request.mock.calls.length).toBeLessThan(25);
});
