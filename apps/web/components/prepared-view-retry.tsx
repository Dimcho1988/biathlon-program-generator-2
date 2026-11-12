"use client";
import {useEffect, useState} from "react";
import {useRouter} from "next/navigation";

export function PreparedViewRetry({compact = false}: {compact?: boolean}) {
  const router = useRouter();
  const [attempt, setAttempt] = useState(0);
  const [waitingTooLong, setWaitingTooLong] = useState(false);
  useEffect(() => {
    const started = Date.now();
    const timer = setInterval(() => {
      if (Date.now() - started >= 120_000) {
        clearInterval(timer);
        setWaitingTooLong(true);
      } else if (document.visibilityState === "visible") router.refresh();
    }, 5000);
    return () => clearInterval(timer);
  }, [router, attempt]);
  return <div className={compact ? "management-preparation-actions" : "integration-actions"}>
    {waitingTooLong && <p role="status">Подготовката отнема повече време от обичайното. Можеш да използваш другите раздели и да провериш отново по-късно.</p>}
    <button type="button" className="action-button secondary" onClick={() => {
      setWaitingTooLong(false); setAttempt(value => value + 1); router.refresh();
    }}>Провери отново</button>
  </div>;
}
