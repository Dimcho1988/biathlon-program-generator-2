"use client";

import { useEffect } from "react";
import { clearSpeedLoadClientCache } from "../lib/speed-load-client";

export function SpeedLoadCacheReset() {
  useEffect(() => { clearSpeedLoadClientCache(); }, []);
  return null;
}
