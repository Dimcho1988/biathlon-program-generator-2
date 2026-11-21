"use client";
import {useEffect, useState} from "react";
import {useRouter} from "next/navigation";
import {useManagementFetch} from "./management-scope";

export function ManagementPreparationRetry({view = "week"}: {view?: "week" | "overview"}) {
  const router = useRouter(), scopedFetch = useManagementFetch();
  const [attempt, setAttempt] = useState(0);
  const [message, setMessage] = useState("");
  useEffect(() => {
    let stopped = false, delay = 5000, refreshedReady = false;
    const started = Date.now();
    let active: AbortController | undefined;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      if (stopped) return;
      if (Date.now() - started >= 15 * 60_000) {
        setMessage("Подготовката продължава. Можеш да провериш отново по-късно."); return;
      }
      if (document.visibilityState === "visible") {
        active = new AbortController();
        const timeout = setTimeout(() => active?.abort(), 15_000);
        try {
          const response = await scopedFetch(`/api/athlete/management/preparation-status?view=${view}`, {
            cache: "no-store", signal: active.signal,
          });
          if (stopped) return;
          if ([401,403,412].includes(response.status)) {
            setMessage("Достъпът или избраният профил е променен. Зареди страницата отново."); return;
          }
          if (!response.ok) throw new Error("status unavailable");
          const state = await response.json();
          if (stopped) return;
          if (state.ready === true && !refreshedReady) router.refresh();
          refreshedReady = state.ready === true;
          setMessage("");
        } catch {
          if (stopped) return;
          setMessage("Връзката временно е прекъсната. Проверяването ще продължи автоматично.");
        } finally { clearTimeout(timeout); }
      }
      delay = Math.min(30_000, delay + 5000);
      timer = setTimeout(poll, delay);
    };
    timer = setTimeout(poll, delay);
    return () => { stopped = true; active?.abort(); clearTimeout(timer); };
  }, [router, scopedFetch, attempt, view]);
  return <div className="management-preparation-actions">
    {message && <p role="status">{message}</p>}
    <button type="button" className="action-button secondary" onClick={() => {
      setMessage(""); setAttempt(value => value + 1); router.refresh();
    }}>Провери отново</button>
  </div>;
}
