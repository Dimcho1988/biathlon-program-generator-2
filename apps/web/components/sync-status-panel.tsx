"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  parseSyncState,
  syncInProgress,
  syncPollDelay,
  syncRequiresViewRefresh,
  syncScopeLabel,
  type SyncState,
} from "../lib/sync";
import { SyncActionForm } from "./sync-action-form";

export function SyncStatusPanel({
  initialState,
  renderedGenerationId,
  returnTo,
}: {
  initialState: SyncState;
  renderedGenerationId: string | null;
  returnTo?: string;
}) {
  const router = useRouter();
  const [state, setState] = useState(initialState);
  const [statusUnavailable, setStatusUnavailable] = useState(false);
  const initialJobId = initialState.job_id;
  const initialStateName = initialState.state;

  useEffect(() => {
    if (syncRequiresViewRefresh(initialState, renderedGenerationId)) {
      router.refresh();
      return;
    }
    if (!syncInProgress(initialState)) return;
    let stopped = false;
    let timer: number | undefined;
    let request: AbortController | undefined;
    let attempt = 0;

    const schedule = () => {
      if (stopped) return;
      timer = window.setTimeout(poll, syncPollDelay(attempt));
      attempt += 1;
    };
    const poll = async () => {
      if (stopped) return;
      if (document.visibilityState === "hidden") {
        schedule();
        return;
      }
      request = new AbortController();
      const timeout = window.setTimeout(() => request?.abort(), 10_000);
      try {
        const response = await fetch("/api/integrations/intervals/status", {
          cache: "no-store",
          headers: { Accept: "application/json" },
          signal: request.signal,
        });
        if (response.status === 401) {
          router.refresh();
          return;
        }
        if (!response.ok) throw new Error(`Sync status failed (${response.status})`);
        const next = parseSyncState(await response.json());
        if (stopped) return;
        setState(next);
        setStatusUnavailable(false);
        if (syncRequiresViewRefresh(next, renderedGenerationId)) {
          router.refresh();
          return;
        }
        if (syncInProgress(next)) schedule();
        else if (next.state === "SUCCEEDED") router.refresh();
      } catch {
        if (!stopped) {
          setStatusUnavailable(true);
          schedule();
        }
      } finally {
        window.clearTimeout(timeout);
      }
    };

    schedule();
    return () => {
      stopped = true;
      if (timer !== undefined) window.clearTimeout(timer);
      request?.abort();
    };
  }, [initialJobId, initialStateName, initialState, renderedGenerationId, router]);

  if (state.state === "IDLE" || (state.state === "SUCCEEDED" && state.active_generation_id === renderedGenerationId))
    return null;

  const scope = syncScopeLabel(state.scope);
  if (state.state === "FAILED") {
    const fullRefreshRequired = state.failure_code === "RECOVERY_SOURCE_REFRESH_REQUIRED";
    return <section className="sync-status failed" role="alert">
      <div>
        <strong>{fullRefreshRequired ? "Необходимо е пълно обновяване" : "Обновяването не завърши"}</strong>
        <span>{fullRefreshRequired
          ? "Записаната история е от по-стара версия и не съдържа необходимите Tref данни. Последната валидна версия остава активна."
          : "Последната валидна версия остава активна. Може да опитате отново."}</span>
      </div>
      {state.scope && <SyncActionForm
        scope={fullRefreshRequired ? "FULL" : state.scope}
        returnTo={returnTo}
        label={fullRefreshRequired ? "Пълно обновяване" : "Опитай отново"}
      />}
    </section>;
  }
  if (state.state === "SUPERSEDED") return <section className="sync-status" role="status">
    <div><strong>Използвана е по-нова заявка</strong><span>Текущият изглед ще следва последната активирана версия.</span></div>
  </section>;
  if (state.state === "SUCCEEDED") return <section className="sync-status completed" role="status">
    <div><strong>Новата версия е активирана</strong><span>Обновяваме показания анализ.</span></div>
  </section>;

  const retry = state.state === "RETRY_WAIT";
  return <section className="sync-status running" role="status" aria-live="polite">
    <div>
      <strong>{retry ? "Изчакваме безопасен повторен опит" : `Обновяваме ${scope}`}</strong>
      <span>
        {state.active_generation_id
          ? `Показана остава последната валидна версия ${state.active_revision}.`
          : "Първата версия се подготвя във фонов режим."}
        {state.stage ? ` Етап: ${state.stage}.` : ""}
      </span>
      {statusUnavailable && <small>Статусът временно не е достъпен; проверката ще продължи автоматично.</small>}
    </div>
    <progress max={100} value={state.progress_percent} aria-label="Напредък на обновяването" />
  </section>;
}
