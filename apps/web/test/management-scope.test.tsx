// @vitest-environment jsdom
import { act, useState } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { ManagementScope, useManagementFetch, SCOPE_CHANGED } from "../components/management-scope";
import { matchesManagementScope } from "../lib/management-scope";
import type { CurrentAthleteAccess } from "../lib/account-access";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); document.body.innerHTML = ""; });
const access = { athleteAlias: "B", actorUserId: "coach" } as CurrentAthleteAccess;
it("rejects equal revisions captured for another athlete or actor, including missing scope", () => {
  for (const [athlete, actor] of [["A", "coach"], ["B", "previous-account"], ["", ""]]) {
    expect(matchesManagementScope(new Request("https://web.test", { headers: { "X-Onflows-Athlete": athlete, "X-Onflows-Actor": actor } }), access)).toBe(false);
  }
  expect(matchesManagementScope(new Request("https://web.test", { headers: { "X-Onflows-Athlete": "B", "X-Onflows-Actor": "coach" } }), access)).toBe(true);
});
function Form() {
  const request = useManagementFetch(); const [message, setMessage] = useState("");
  return <><button onClick={async () => {
    try { const response = await request("/api/athlete/management/activate", {method: "POST", body: JSON.stringify({ expected_revision: 1 })}); setMessage((await response.json()).message); }
    catch (error) { setMessage(error instanceof Error ? error.message : "error"); }
  }}>Approve</button><p>{message}</p></>;
}
it("captures scope, discards a late response after account switching, and never replays approval", async () => {
  let resolve!: (value: Response) => void;
  const request = vi.fn<typeof fetch>(() => new Promise<Response>(r => { resolve = r; })); vi.stubGlobal("fetch", request);
  const element = document.createElement("div"); document.body.append(element); const root = createRoot(element);
  await act(async () => root.render(<ManagementScope athleteAlias="A" actorUserId="coach"><Form /></ManagementScope>));
  await act(async () => element.querySelector("button")!.click());
  expect(request).toHaveBeenCalledTimes(1);
  const headers = request.mock.calls[0][1]!.headers as Headers;
  expect(headers.get("X-Onflows-Athlete")).toBe("A"); expect(headers.get("X-Onflows-Actor")).toBe("coach");
  await act(async () => window.dispatchEvent(new Event("onflows-scope-changed")));
  await act(async () => resolve(Response.json({ message: "Old athlete result" })));
  expect(element.textContent).toContain(SCOPE_CHANGED); expect(element.textContent).not.toContain("Old athlete result");
  expect(request).toHaveBeenCalledTimes(1);
  await act(async () => root.unmount());
});
