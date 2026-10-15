"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";

type Scope = { athleteAlias: string; actorUserId: string };
const Context = createContext<typeof fetch | null>(null);
export const SCOPE_CHANGED = "Профилът е сменен в друг прозорец. Зареди актуалния екран преди следващо действие.";

export function ManagementScope({ athleteAlias, actorUserId, children }: Scope & { children: ReactNode }) {
  const [changed, setChanged] = useState(false);
  const invalid = useRef(false);
  const alive = useRef(true);
  const key = `${actorUserId}:${athleteAlias}`;
  useEffect(() => {
    alive.current = true;
    const channel = typeof BroadcastChannel === "undefined" ? null : new BroadcastChannel("onflows-management-scope");
    const invalidate = () => { invalid.current = true; setChanged(true); };
    window.addEventListener("onflows-scope-changed", invalidate);
    if (channel) {
      channel.onmessage = event => {
        if (event.data?.changed || event.data?.scope && event.data.scope !== key) { invalid.current = true; setChanged(true); }
      };
      channel.postMessage({ scope: key });
    }
    return () => { alive.current = false; channel?.close(); window.removeEventListener("onflows-scope-changed", invalidate); };
  }, [key]);
  const scopedFetch = useCallback<typeof fetch>(async (input, init) => {
    if (invalid.current || !alive.current) throw new Error(SCOPE_CHANGED);
    const headers = new Headers(init?.headers);
    headers.set("X-Onflows-Athlete", athleteAlias);
    headers.set("X-Onflows-Actor", actorUserId);
    const response = await fetch(input, { ...init, headers });
    if (!alive.current || invalid.current) throw new Error(SCOPE_CHANGED);
    if (response.status === 412 || response.status === 401) {
      invalid.current = true; setChanged(true); throw new Error(SCOPE_CHANGED);
    }
    return response;
  }, [athleteAlias, actorUserId]);
  return <Context.Provider value={scopedFetch}>{changed ? <section className="management-notice" role="alert"><p>{SCOPE_CHANGED}</p><button type="button" onClick={() => window.location.reload()}>Зареди актуалния профил</button></section> : children}</Context.Provider>;
}

export function useManagementFetch(): typeof fetch {
  // Standalone fixtures can omit the provider; live routes reject missing scope.
  return useContext(Context) ?? fetch;
}
