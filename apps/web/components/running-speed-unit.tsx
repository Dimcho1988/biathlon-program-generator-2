"use client";

import { useSyncExternalStore } from "react";
import { RUNNING_SPEED_UNIT_KEY, type RunningSpeedUnit, speedText } from "../lib/speed-format";

const eventName = "onflows-running-speed-unit-change";
let memoryUnit: RunningSpeedUnit = "pace";
function snapshot(): RunningSpeedUnit {
  try { const value = window.localStorage.getItem(RUNNING_SPEED_UNIT_KEY); return value === "kmh" ? "kmh" : value === "pace" ? "pace" : memoryUnit; }
  catch { return memoryUnit; }
}
function subscribe(callback: () => void) {
  window.addEventListener("storage", callback); window.addEventListener(eventName, callback);
  return () => { window.removeEventListener("storage", callback); window.removeEventListener(eventName, callback); };
}
export function setRunningSpeedUnit(unit: RunningSpeedUnit) {
  memoryUnit = unit;
  try { window.localStorage.setItem(RUNNING_SPEED_UNIT_KEY, unit); } catch { /* Session preference still works when storage is unavailable. */ }
  window.dispatchEvent(new Event(eventName));
}
export function useRunningSpeedUnit() { return useSyncExternalStore(subscribe, snapshot, () => "pace" as const); }
export function RunningSpeedUnitControl() {
  const unit = useRunningSpeedUnit();
  return <label className="running-speed-unit">Бягане · показване <select value={unit} onChange={e => setRunningSpeedUnit(e.target.value as RunningSpeedUnit)}><option value="pace">Темпо, мин/км</option><option value="kmh">Скорост, км/ч</option></select></label>;
}
export function PreferredSpeed({ speed, sport }: { speed?: number | null; sport?: string | null }) {
  return <>{speedText(speed, useRunningSpeedUnit(), sport)}</>;
}
