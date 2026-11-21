"use client";

import { useEffect } from "react";
import { API_WAKE_RETURN_KEY, wakeReturnHref } from "../lib/api-wake";

export function WakeMarkerCleaner() {
  useEffect(() => {
    const url = new URL(window.location.href);
    if (!url.searchParams.has("wake")) return;
    if (url.searchParams.get("wake") === "ready") {
      try {
        const saved = sessionStorage.getItem(API_WAKE_RETURN_KEY);
        sessionStorage.removeItem(API_WAKE_RETURN_KEY);
        const destination = wakeReturnHref(saved);
        if (saved && destination !== "/") {
          window.location.replace(destination);
          return;
        }
      } catch { /* The overview remains usable without browser storage. */ }
    }
    url.searchParams.delete("wake");
    window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
  }, []);
  return null;
}
