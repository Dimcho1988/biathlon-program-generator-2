"use client";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import type { RacePreparation } from "../lib/energy";

const n = (value: number | null) => value == null ? "—" : value.toLocaleString("bg-BG", { maximumFractionDigits: 0 });

function Proposal({ proposal: p, canEdit }: { proposal: RacePreparation; canEdit: boolean }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [saved, setSaved] = useState(false);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    const clear = () => { alive.current = false; setSaved(true); setMessage(""); };
    window.addEventListener("onflows-scope-changed", clear);
    const channel = typeof BroadcastChannel !== "undefined" ? new BroadcastChannel("onflows-management-scope") : null;
    if (channel) channel.onmessage = clear;
    return () => { alive.current = false; window.removeEventListener("onflows-scope-changed", clear); channel?.close(); };
  }, []);
  async function decide(action: "ACCEPTED" | "DECLINED") {
    setBusy(true); setMessage("");
    try {
      const response = await fetch("/api/athlete/energy/race-loading", { method: "POST",
        headers: { "Content-Type": "application/json" }, signal: AbortSignal.timeout(80000),
        body: JSON.stringify({ event_id: p.event_id, preview_hash: p.preview_hash,
          expected_revision: p.revision, action }) });
      const value = await response.json();
      if (!response.ok || !value.saved) throw new Error(value.error || "Записът не е потвърден.");
      if (!alive.current) return;
      setSaved(true);
      setMessage(action === "ACCEPTED" ? "Зареждането е прието за показаните дни. Целите се обновяват." : "Избран е обичайният хранителен режим.");
      window.dispatchEvent(new Event("onflows-nutrition-changed"));
      router.refresh();
    } catch (error) { if (alive.current) setMessage(error instanceof Error ? error.message : "Записът не успя."); }
    finally { if (alive.current) setBusy(false); }
  }
  return <article>
    <h3>{p.event_name} · {p.race_date}</h3>
    <p>{p.status === "ACCEPTED" ? "Прието зареждане" : p.status === "DECLINED" ? "Избран е обичайният режим" : "Предложение за въглехидратно зареждане"}</p>
    <p>Начална цел 10 g/kg/ден за двата дни преди старта. Белтъчините се запазват; мазнините временно са 20–25% от енергията. След показаните дни отново важат обичайните цели с 25% мазнини.</p>
    {p.late && <p className="energy-notice">Остава само днешният ден. Това не представлява пълен протокол за 36–48 часа; не наваксвайте пропуснатия прием наведнъж.</p>}
    {p.days.length > 0 && <div className="energy-table" tabIndex={0}>
      <table><caption>Обичайна цел → предложение за всеки ден</caption>
        <thead><tr><th>Дата</th><th>kcal</th><th>Въглехидрати · g</th><th>Мазнини · g</th><th>Белтъчини · g</th></tr></thead>
        <tbody>{p.days.map(d => <tr key={d.day}><th scope="row">{d.day}</th>
          <td>{n(d.base_targets.kcal)} → {n(d.targets.kcal)} (+{n((d.targets.kcal ?? 0) - (d.base_targets.kcal ?? 0))})</td>
          <td>{n(d.base_targets.carbohydrate_g)} → {n(d.targets.carbohydrate_g)}</td>
          <td>{n(d.base_targets.fat_g)} → {n(d.targets.fat_g)}</td>
          <td>{n(d.targets.protein_g)}</td></tr>)}</tbody>
      </table>
    </div>}
    <p className="energy-caption">Храната, геловете и напитките се включват в дневния сбор. Използвайте познати продукти според поносимостта; ИИ може да разпредели количествата по хранения.</p>
    {p.reason && <p>{p.reason}</p>}
    {canEdit && <div className="energy-heading">
      {p.status !== "ACCEPTED" && <button className="action-button" disabled={busy || saved || !p.can_accept} onClick={() => decide("ACCEPTED")}>Приеми за показаните дни</button>}
      {p.status !== "DECLINED" && <button className="action-button secondary" disabled={busy || saved} onClick={() => decide("DECLINED")}>{p.status === "ACCEPTED" ? "Върни обичайния режим" : "Запази обичайния режим"}</button>}
    </div>}
    <p role="status">{message}</p>
    <Link href="/assistant" className="action-button secondary">Обсъди зареждането с ИИ</Link>
  </article>;
}

export function RaceNutrition({ proposals, canEdit }: { proposals: RacePreparation[]; canEdit: boolean }) {
  if (!proposals.length) return null;
  return <section className="energy-panel" id="race-preparation"><h2>Хранене преди състезание</h2>
    {proposals.map(p => <Proposal key={`${p.event_id}-${p.preview_hash}-${p.status}`} proposal={p} canEdit={canEdit} />)}
  </section>;
}

export function NutritionReminder() {
  const [events, setEvents] = useState<{ event_id: string; name: string; race_date: string }[]>([]);
  useEffect(() => {
    let stopped = false;
    let controller: AbortController | null = null;
    const clear = () => { stopped = true; controller?.abort(); setEvents([]); };
    const refresh = async () => {
      if (stopped || document.visibilityState === "hidden") return;
      controller?.abort(); controller = new AbortController();
      const request = controller;
      try {
        const r = await fetch("/api/athlete/energy/race-reminders", { cache: "no-store", signal: request.signal });
        const data = r.ok ? await r.json() : { events: [] };
        if (!stopped && !request.signal.aborted) setEvents(Array.isArray(data.events) ? data.events : []);
      } catch { if (!stopped && !request.signal.aborted) setEvents([]); }
    };
    void refresh();
    const timer = window.setInterval(refresh, 300000);
    window.addEventListener("focus", refresh);
    window.addEventListener("onflows-nutrition-changed", refresh);
    window.addEventListener("onflows-scope-changed", clear);
    const channel = typeof BroadcastChannel !== "undefined" ? new BroadcastChannel("onflows-management-scope") : null;
    if (channel) channel.onmessage = clear;
    return () => { stopped = true; controller?.abort(); window.clearInterval(timer);
      window.removeEventListener("focus", refresh); window.removeEventListener("onflows-nutrition-changed", refresh);
      window.removeEventListener("onflows-scope-changed", clear); channel?.close(); };
  }, []);
  if (!events.length) return null;
  return <aside role="status" style={{ margin: "1rem", padding: "1rem", border: "1px solid #159b8d", borderRadius: "12px" }}>
    Предстои продължителен старт: {events.map(e => `${e.name} (${e.race_date})`).join(", ")}.{" "}
    <Link href="/energy#race-preparation">Прегледай предложението за хранене</Link>. Промяна се прави само след твоя избор.
  </aside>;
}
