"use client";
import { useEffect } from "react";

// Announce before navigation so late responses cannot repaint an old athlete.
export function ScopeChangeNotifier() {
  useEffect(() => {
    const notify = (event: SubmitEvent) => {
      if (!(event.target instanceof HTMLFormElement)) return;
      const path = new URL(event.target.action, window.location.href).pathname;
      if (path !== "/api/account/athletes/select" && !path.startsWith("/api/auth/")) return;
      window.dispatchEvent(new Event("onflows-scope-changed"));
      if (typeof BroadcastChannel !== "undefined") {
        const channel = new BroadcastChannel("onflows-management-scope");
        channel.postMessage({ changed: true }); channel.close();
      }
    };
    document.addEventListener("submit", notify, true);
    return () => document.removeEventListener("submit", notify, true);
  }, []);
  return null;
}
