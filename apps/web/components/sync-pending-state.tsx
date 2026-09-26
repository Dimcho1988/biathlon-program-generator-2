import type { SyncState } from "../lib/sync";
import { SyncStatusPanel } from "./sync-status-panel";

export function SyncPendingState({ state }: { state: SyncState }) {
  return <main className="state-page" aria-busy="true">
    <div className="loader" aria-hidden="true" />
    <p className="eyebrow">onFlows · синхронизация</p>
    <h1>Подготвяме първия тренировъчен анализ</h1>
    <p className="muted">Може да затворите страницата. Задачата продължава във фонов режим и резултатът ще бъде достъпен от всеки браузър с този профил.</p>
    <SyncStatusPanel key={state.job_id ?? "idle"} initialState={state} renderedGenerationId={null} />
  </main>;
}
