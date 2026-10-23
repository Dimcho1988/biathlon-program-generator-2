"use client";
import { useManagementFetch } from "./management-scope";
import { durationHms } from "../lib/duration-format";
import { useState } from "react";
import { isRecord } from "../lib/training-status";
import {lactateRangeText} from "../lib/training-observations";
export function RaceDurationSummary({value}: {value: unknown}) {
  if (!isRecord(value) || typeof value.duration_min !== "number") return null;
  const ref=isRecord(value.specific_reference)?value.specific_reference:null;
  const bands=Array.isArray(ref?.bands)?ref.bands.filter(isRecord):[];
  const n=(v:number)=>v.toLocaleString("bg-BG",{maximumFractionDigits:2});
  const timeRange = Array.isArray(value.duration_range_min) ? value.duration_range_min as number[] : null;
  const pace = (v: number) => { const seconds = Math.round(3600 / v); return `${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,"0")}`; };
  const course = isRecord(ref?.course) ? ref.course : null;
  const terrain = course && (course.profile !== "FLAT" || !["ROAD", "TRACK"].includes(String(course.surface)));
  return <><p>Продължителност за планиране: <strong>{timeRange ? timeRange.map(durationHms).join("–") : `около ${durationHms(value.duration_min)}`}</strong> · {value.source === "SPEED_DURATION" ? "индивидуален модел скорост–време" : value.source === "COURSE_EXPECTATION" ? "очакван диапазон за конкретното трасе" : "въведена ръчно"}. {value.source === "SPEED_DURATION" && "Оценката е за движение; релеф, условия и стрелба могат да променят времето."}</p>
    {terrain && <p>Темпото по-долу е равнинен еквивалент на усилието. На състезателния терен водят времето и усилието.</p>}
    {bands.length>0&&<details><summary>Темпо и лактат около състезателната дисциплина</summary><div className="management-table-wrap"><table><thead><tr><th>Работен ориентир</th><th>Скорост</th><th>Темпо / км</th><th>Лактат по личния тест</th></tr></thead><tbody>{bands.map(b=>{const la=isRecord(b.lactate_reference)?b.lactate_reference:null;const speeds = Array.isArray(b.speed_range_kmh) ? b.speed_range_kmh as number[] : typeof b.speed_kmh === "number" ? [b.speed_kmh] : []; return <tr key={String(b.role)}><th>{String(b.label)}</th><td>{speeds.length ? `${speeds.map(n).join("–")} км/ч` : "—"}</td><td>{[...speeds].reverse().map(pace).join("–") || "—"}</td><td>{la&&typeof la.estimated_mmol==="number"?lactateRangeText({low_mmol:la.estimated_mmol,high_mmol:la.estimated_mmol}):"Няма измерена опора при тази скорост"}</td></tr>;})}</tbody></table></div><p>Скоростите са ориентири за избора на метод. Продължителността на отсечките, почивките и общата доза се определят отделно. Лактатните стойности се отнасят за протокола на личния тест при горната скоростна граница.</p></details>}</>;
}
export function RaceDurationEstimate({sport, discipline, fallback}: {sport: string; discipline: string; fallback: number | null}) {
  const scopedFetch = useManagementFetch();
  const [result, setResult] = useState<unknown>(null), [busy, setBusy] = useState(false), [error, setError] = useState("");
  async function estimate() {
    setBusy(true); setError(""); setResult(null);
    try {
      const response = await scopedFetch("/api/athlete/management/race-duration", {method: "POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({sport,discipline,race_duration_min:fallback})});
      const value: unknown = await response.json();
      if (!response.ok || !isRecord(value)) throw new Error("Оценката временно не е достъпна. Можеш да въведеш приблизително време ръчно.");
      setResult(value);
    } catch (e) {setError(e instanceof Error ? e.message : "Оценката не е достъпна.");}
    finally {setBusy(false);}
  }
  return <div className="management-notice"><p>Системата изчислява времето автоматично от дистанцията и индивидуалния модел за основния спорт. Ако няма подходяща крива, използва въведеното резервно време.</p><button className="action-button secondary" type="button" disabled={busy || !discipline.trim()} onClick={estimate}>{busy ? "Изчисляване…" : "Провери приблизителното време"}</button><div role="status"><RaceDurationSummary value={result}/>{isRecord(result) && result.source !== "SPEED_DURATION" && <p>{result.reason === "DISTANCE_REQUIRED" ? "Въведи една дистанция с мерна единица, например 7.5 km или 1500 m." : "Няма подкрепена индивидуална оценка за тази дистанция и спорт."} {result.source === "MANUAL" ? "За планирането ще се използва ръчното време." : "Въведи приблизителна продължителност по-долу."}</p>}{error && <p>{error}</p>}</div></div>;
}
