"use client";
import {useEffect, useState} from "react";
import {useRouter} from "next/navigation";

export function PreparedViewRetry({compact = false}: {compact?: boolean}) {
  const router = useRouter();
  const [attempt, setAttempt] = useState(0);
  const [waitingTooLong, setWaitingTooLong] = useState(false);
  useEffect(() => {
    const started = Date.now();
    let delay = 5000;
    let timer: ReturnType<typeof setTimeout>;
    const poll = () => {
      if (Date.now() - started >= 120_000) {
        setWaitingTooLong(true);
        return;
      }
      if (document.visibilityState === "visible") router.refresh();
      // A server refresh reloads the page's sources. Space those reads while
      // keeping a bounded wait and an immediate manual retry.
      delay = Math.min(20_000, delay + 5000);
      timer = setTimeout(poll, Math.min(delay, 120_000 - (Date.now() - started)));
    };
    timer = setTimeout(poll, delay);
    return () => clearTimeout(timer);
  }, [router, attempt]);
  return <div className={compact ? "management-preparation-actions" : "integration-actions"}>
    {waitingTooLong && <p role="status">Подготовката отнема повече време от обичайното. Можеш да използваш другите раздели и да провериш отново по-късно.</p>}
    <button type="button" className="action-button secondary" onClick={() => {
      setWaitingTooLong(false); setAttempt(value => value + 1); router.refresh();
    }}>Провери отново</button>
  </div>;
}
