"use client";

import { useEffect } from "react";

export function WakeMarkerCleaner() {
  useEffect(() => {
    const url = new URL(window.location.href);
    if (!url.searchParams.has("wake")) return;
    url.searchParams.delete("wake");
    window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
  }, []);
  return null;
}
