"use client";

import { useEffect, useState } from "react";
import { API_WAKE_RETRY_KEY, API_WAKE_RETURN_KEY, wakeReturnHref } from "../lib/api-wake";

const RETRY_DELAY_SECONDS = 15;
const MAX_AUTOMATIC_RETRIES = 3;
const RETRY_WINDOW_MS = 15 * 60 * 1000;
const STORAGE_KEY = API_WAKE_RETRY_KEY;

interface RetryState {
  startedAt: number;
  attempts: number;
}

function storedRetryState(now: number): RetryState {
  try {
    const parsed = JSON.parse(sessionStorage.getItem(STORAGE_KEY) ?? "null") as Partial<RetryState> | null;
    if (
      parsed &&
      typeof parsed.startedAt === "number" &&
      typeof parsed.attempts === "number" &&
      Number.isInteger(parsed.attempts) &&
      parsed.attempts >= 0 &&
      now - parsed.startedAt < RETRY_WINDOW_MS
    ) return { startedAt: parsed.startedAt, attempts: parsed.attempts };
  } catch {
    // A malformed or unavailable session store must never block manual retry.
  }
  return { startedAt: now, attempts: 0 };
}

export function ApiWakeRetry({ wakeHref, retryHref = "/", automatic = true }: { wakeHref?: string; retryHref?: string; automatic?: boolean }) {
  const delaySeconds = wakeHref ? 3 : RETRY_DELAY_SECONDS;
  const maxAttempts = wakeHref ? 1 : MAX_AUTOMATIC_RETRIES;
  const [seconds, setSeconds] = useState(delaySeconds);
  const [attempt, setAttempt] = useState<number | null>(null);

  const recover = (manual: boolean) => {
    try {
      if (manual) sessionStorage.removeItem(STORAGE_KEY);
      if (wakeHref) sessionStorage.setItem(API_WAKE_RETURN_KEY, wakeReturnHref(`${window.location.pathname}${window.location.search}`));
    } catch { /* Manual navigation remains available without browser storage. */ }
    window.location.assign(wakeHref ?? retryHref);
  };

  useEffect(() => {
    const state = storedRetryState(Date.now());
    if (!automatic || state.attempts >= maxAttempts) {
      const finished = window.setTimeout(() => setAttempt(0), 0);
      return () => window.clearTimeout(finished);
    }

    const nextAttempt = state.attempts + 1;
    const initialized = window.setTimeout(() => setAttempt(nextAttempt), 0);
    let canRemember = true;
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify({
        startedAt: state.startedAt,
        attempts: state.attempts,
      }));
    } catch {
      canRemember = false;
    }

    // Without persistent attempt state a cross-origin return could loop forever.
    if (!canRemember) {
      window.clearTimeout(initialized);
      const stopped = window.setTimeout(() => setAttempt(0), 0);
      return () => window.clearTimeout(stopped);
    }

    const countdown = window.setInterval(
      () => setSeconds((value) => Math.max(0, value - 1)),
      1_000,
    );
    const retry = window.setTimeout(() => {
      try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ startedAt: state.startedAt, attempts: nextAttempt })); }
      catch { return; }
      if (wakeHref) {
        try { sessionStorage.setItem(API_WAKE_RETURN_KEY, wakeReturnHref(`${window.location.pathname}${window.location.search}`)); }
        catch { return; }
        window.location.assign(wakeHref);
      } else window.location.reload();
    }, delaySeconds * 1_000);
    return () => {
      window.clearTimeout(initialized);
      window.clearInterval(countdown);
      window.clearTimeout(retry);
    };
  }, [automatic, delaySeconds, maxAttempts, wakeHref]);

  return <>
    <div className="integration-actions"><a className="action-button" href={wakeHref ?? retryHref} onClick={(event) => { event.preventDefault(); recover(true); }}>Опитай отново</a></div>
    <p className="wake-retry" aria-live="polite">{attempt === null
      ? automatic ? "Подготвяме автоматичен повторен опит…" : "Изчакай малко преди следващия опит."
      : attempt === 0 ? "Автоматичният опит приключи. Можеш да опиташ отново след малко."
      : wakeHref ? `Стартираме услугата за данни след ${seconds} сек. Ще се върнеш автоматично в приложението.`
      : `Нов автоматичен опит след ${seconds} сек. (${attempt}/${maxAttempts})`}</p>
  </>;
}
