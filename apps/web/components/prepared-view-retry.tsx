"use client";
import {useEffect, useState} from "react";
import {useRouter} from "next/navigation";

export function PreparedViewRetry({compact = false, keepPolling = false}: {compact?: boolean; keepPolling?: boolean}) {
  const router = useRouter();
  const [attempt, setAttempt] = useState(0);
  const [waitingTooLong, setWaitingTooLong] = useState(false);
  useEffect(() => {
    const started = Date.now();
    let delay = 5000;
    let timer: ReturnType<typeof setTimeout>;
    const poll = () => {
      if (!keepPolling && Date.now() - started >= 120_000) {
        setWaitingTooLong(true);
      }
      if (document.visibilityState === "visible") router.refresh();
      // A server refresh reloads the page's sources. Space those reads while
      // slowing down after a long wait without abandoning the pending page.
      delay = Math.min(20_000, delay + 5000);
      const elapsed = Date.now() - started;
      timer = setTimeout(poll, keepPolling ? delay : elapsed >= 120_000 ? 60_000 : Math.min(delay, 120_000 - elapsed));
    };
    timer = setTimeout(poll, delay);
    return () => clearTimeout(timer);
  }, [router, attempt, keepPolling]);
  return <div className={compact ? "management-preparation-actions" : "integration-actions"}>
    {waitingTooLong && <p role="status">Подготовката отнема повече време от обичайното. Можеш да използваш другите раздели и да провериш отново по-късно.</p>}
    <button type="button" className="action-button secondary" onClick={() => {
      setWaitingTooLong(false); setAttempt(value => value + 1); router.refresh();
    }}>Провери отново</button>
  </div>;
}
