import Link from "next/link";
import { ApiWakeRetry } from "./api-wake-retry";
import { SyncActionForm } from "./sync-action-form";

export function ErrorState({ message, integrationActions = false, connectAvailable = true, profileSelectionAvailable = false, retryAvailable = false, retryHref = "/", refreshAvailable = true, notice }: {
  message: string;
  integrationActions?: boolean;
  connectAvailable?: boolean;
  profileSelectionAvailable?: boolean;
  retryAvailable?: boolean;
  retryHref?: string;
  refreshAvailable?: boolean;
  notice?: string;
}) {
  const apiWakeTimedOut = message === "API услугата не се събуди навреме.";
  return (
    <main className="state-page" role="alert">
      <span className="state-icon" aria-hidden="true">!</span>
      <p className="eyebrow">Данните не са заредени</p>
      <h1>Не можем да покажем тренировъчния статус</h1>
      <p className="muted">{message}</p>
      {notice && <p className="connection-notice">{notice}</p>}
      {(integrationActions || profileSelectionAvailable || retryAvailable) && <div className="integration-actions">
        {profileSelectionAvailable && <Link className="action-button" href="/account">Избери достъпен профил</Link>}
        {integrationActions && connectAvailable && <a className={`action-button${profileSelectionAvailable ? " secondary" : ""}`} href="/api/integrations/intervals/connect">Свържи нов Intervals профил</a>}
        {retryAvailable && <Link className="action-button" href={retryHref}>Опитай отново</Link>}
        {integrationActions && refreshAvailable && <SyncActionForm label="Обнови реалните данни" />}
      </div>}
      {profileSelectionAvailable && integrationActions && connectAvailable && <p className="state-help">Ако профилът вече е добавен към акаунта, избери го от списъка. Ново свързване с Intervals е необходимо само при добавяне на друг профил.</p>}
      {apiWakeTimedOut && <ApiWakeRetry />}
      <p className="state-help">Free preview се събужда автоматично. При по-бавен старт изчакването може да достигне около две минути; не е нужно да свързвате профила отново.</p>
    </main>
  );
}
