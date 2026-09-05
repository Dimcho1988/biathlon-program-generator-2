"use client";

import { useEffect, useState } from "react";

const RETRY_DELAY_SECONDS = 15;
const MAX_AUTOMATIC_RETRIES = 3;
const RETRY_WINDOW_MS = 15 * 60 * 1000;
const STORAGE_KEY = "onflows-api-wake-retry";

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

export function ApiWakeRetry() {
  const [seconds, setSeconds] = useState(RETRY_DELAY_SECONDS);
  const [attempt, setAttempt] = useState<number | null>(null);

  useEffect(() => {
    const state = storedRetryState(Date.now());
    if (state.attempts >= MAX_AUTOMATIC_RETRIES) {
      const finished = window.setTimeout(() => setAttempt(0), 0);
      return () => window.clearTimeout(finished);
    }

    const nextAttempt = state.attempts + 1;
    const initialized = window.setTimeout(() => setAttempt(nextAttempt), 0);
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify({
        startedAt: state.startedAt,
        attempts: nextAttempt,
      }));
    } catch {
      // Continue with the in-memory attempt when storage is unavailable.
    }

    const countdown = window.setInterval(
      () => setSeconds((value) => Math.max(0, value - 1)),
      1_000,
    );
    const retry = window.setTimeout(() => window.location.reload(), RETRY_DELAY_SECONDS * 1_000);
    return () => {
      window.clearTimeout(initialized);
      window.clearInterval(countdown);
      window.clearTimeout(retry);
    };
  }, []);

  if (attempt === null) return <p className="wake-retry" aria-live="polite">Подготвяме автоматичен повторен опит…</p>;
  if (attempt === 0) return <p className="wake-retry" aria-live="polite">Автоматичните опити приключиха. Използвайте „Опитай отново“ или проверете Render.</p>;
  return <p className="wake-retry" aria-live="polite">Нов автоматичен опит след {seconds} сек. ({attempt}/{MAX_AUTOMATIC_RETRIES})</p>;
}
