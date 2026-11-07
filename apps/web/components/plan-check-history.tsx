"use client";

import { useEffect, useRef, useState } from "react";
import { parsePlanChecks, type PlanCheckEvent } from "../lib/management-checks";
import { isRecord } from "../lib/training-status";
import { useManagementFetch } from "./management-scope";

const uniqueChecks = (rows: PlanCheckEvent[]) => {
  const ids = new Set<string>();
  return rows.filter(row => {
    if (ids.has(row.id.toLowerCase())) return false;
    ids.add(row.id.toLowerCase());
    return true;
  });
};

export function PlanCheckHistory({ refreshKey }: { refreshKey: number }) {
  const scopedFetch = useManagementFetch();
  const [query, setQuery] = useState({ open: false, iteration: 0, before: null as number | null, scope: scopedFetch, refreshKey });
  const [history, setHistory] = useState({
    scope: scopedFetch, iteration: -1, refreshKey: -1, checks: [] as PlanCheckEvent[], nextBefore: null as number | null,
    loaded: false, failure: null as { message: string; before: number | null } | null,
  });
  const sequence = useRef(0);
  const sameScope = history.scope === scopedFetch;
  const loading = query.open && (!sameScope || history.iteration !== query.iteration || history.refreshKey !== refreshKey);
  const failure = sameScope && !loading ? history.failure : null;
  const load = (before: number | null = null) => setQuery(current => ({ ...current, iteration: current.iteration + 1, before, scope: scopedFetch, refreshKey }));

  useEffect(() => {
    if (!query.open) return;
    const requestId = ++sequence.current;
    const controller = new AbortController();
    const { iteration } = query;
    const before = query.scope === scopedFetch && query.refreshKey === refreshKey ? query.before : null;
    void (async () => {
      try {
        const response = await scopedFetch(`/api/athlete/management/checks?limit=10${before === null ? "" : `&before=${before}`}`, {
          method: "GET", cache: "no-store", signal: controller.signal,
        });
        const value: unknown = await response.json();
        if (requestId !== sequence.current || controller.signal.aborted) return;
        if (!response.ok) throw new Error(isRecord(value) && typeof value.error === "string" ? value.error : "Проверките временно не са достъпни.");
        const result = parsePlanChecks(value);
        if (before !== null && result.checks.some(check => check.sequence >= before)) throw new Error("Невалидна следваща страница с проверки.");
        setHistory(current => ({
          scope: scopedFetch, iteration, refreshKey, loaded: true, failure: null, nextBefore: result.next_before,
          checks: uniqueChecks(before === null ? result.checks : [...(current.scope === scopedFetch ? current.checks : []), ...result.checks]),
        }));
      } catch (caught) {
        if (requestId === sequence.current && !controller.signal.aborted) setHistory(current => ({
          scope: scopedFetch, iteration, refreshKey, checks: current.scope === scopedFetch ? current.checks : [],
          nextBefore: current.scope === scopedFetch ? current.nextBefore : null, loaded: current.scope === scopedFetch && current.loaded,
          failure: { message: caught instanceof Error ? caught.message : "Проверките временно не са достъпни.", before },
        }));
      }
    })();
    return () => { sequence.current += 1; controller.abort(); };
  }, [query, refreshKey, scopedFetch]);

  return <details className="management-panel" onToggle={event => {
    const open = event.currentTarget.open;
    setQuery(current => current.open === open ? current : { open, iteration: current.iteration + 1, before: null, scope: scopedFetch, refreshKey });
  }}>
    <summary>Проверки без промяна</summary>
    {query.open && <>
      <p className="management-muted">Тук се записва кога преизчисляването не е променило проверената версия. Това не утвърждава предложение и не създава нова версия на програмата.</p>
      <button type="button" className="action-button secondary" disabled={loading} onClick={() => load()}>Обнови проверките</button>
      {loading && <p role="status">Зареждане на проверките…</p>}
      {failure && <div className="management-error" role="alert"><p>{failure.message}</p>
        <button type="button" className="action-button secondary" disabled={loading} onClick={() => load(failure.before)}>Опитай отново</button>
      </div>}
      {sameScope && history.loaded && history.checks.length === 0 && <p>Няма записани проверки без промяна.</p>}
      {sameScope && history.checks.length > 0 && <ol>{history.checks.map(check => <li key={check.id}>
        <strong>Проверка без промяна · версия {check.checked_revision}</strong> · <time dateTime={check.recorded_at}>{new Date(check.recorded_at).toLocaleString("bg-BG")}</time>
        <p>{check.reason}</p>
      </li>)}</ol>}
      {sameScope && history.nextBefore !== null && <button type="button" className="action-button secondary" disabled={loading} onClick={() => load(history.nextBefore)}>Още проверки</button>}
    </>}
  </details>;
}
