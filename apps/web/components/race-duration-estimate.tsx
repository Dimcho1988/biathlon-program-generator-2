"use client";
import { useState } from "react";
import { isRecord } from "../lib/training-status";
export function RaceDurationSummary({value}: {value: unknown}) {
  if (!isRecord(value) || typeof value.duration_min !== "number") return null;
  return <p>Продължителност за планиране: <strong>около {value.duration_min.toLocaleString("bg-BG", {maximumFractionDigits:1})} мин</strong> · {value.source === "SPEED_DURATION" ? "индивидуален модел скорост–време" : "въведена ръчно"}. {value.source === "SPEED_DURATION" && "Оценката е за движение; релеф, условия и стрелба могат да променят времето."}</p>;
}
export function RaceDurationEstimate({sport, discipline, fallback}: {sport: string; discipline: string; fallback: number | null}) {
  const [result, setResult] = useState<unknown>(null), [busy, setBusy] = useState(false), [error, setError] = useState("");
  async function estimate() {
    setBusy(true); setError(""); setResult(null);
    try {
      const response = await fetch("/api/athlete/management/race-duration", {method: "POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({sport,discipline,race_duration_min:fallback})});
      const value: unknown = await response.json();
      if (!response.ok || !isRecord(value)) throw new Error("Оценката временно не е достъпна. Можеш да въведеш приблизително време ръчно.");
      setResult(value);
    } catch (e) {setError(e instanceof Error ? e.message : "Оценката не е достъпна.");}
    finally {setBusy(false);}
  }
  return <div className="management-notice"><p>Системата изчислява времето автоматично от дистанцията и индивидуалния модел за основния спорт. Ако няма подходяща крива, използва въведеното резервно време.</p><button className="action-button secondary" type="button" disabled={busy || !discipline.trim()} onClick={estimate}>{busy ? "Изчисляване…" : "Провери приблизителното време"}</button><div role="status"><RaceDurationSummary value={result}/>{isRecord(result) && result.source !== "SPEED_DURATION" && <p>{result.reason === "DISTANCE_REQUIRED" ? "Въведи една дистанция с мерна единица, например 7.5 km или 1500 m." : "Няма подкрепена индивидуална оценка за тази дистанция и спорт."} {result.source === "MANUAL" ? "За планирането ще се използва ръчното време." : "Въведи приблизителна продължителност по-долу."}</p>}{error && <p>{error}</p>}</div></div>;
}
