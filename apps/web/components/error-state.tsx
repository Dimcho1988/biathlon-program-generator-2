import Link from "next/link";
import { ApiWakeRetry } from "./api-wake-retry";

export function ErrorState({ message, integrationActions = false, connectAvailable = true, retryAvailable = false, refreshAvailable = true, notice }: {
  message: string;
  integrationActions?: boolean;
  connectAvailable?: boolean;
  retryAvailable?: boolean;
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
      {integrationActions && <div className="integration-actions">
        {connectAvailable && <a className="action-button" href="/api/integrations/intervals/connect">Свържи Intervals</a>}
        {retryAvailable && <Link className="action-button" href="/">Опитай отново</Link>}
        {refreshAvailable && <form action="/api/integrations/intervals/refresh" method="post"><button className="action-button secondary" type="submit">Обнови реалните данни</button></form>}
      </div>}
      {apiWakeTimedOut && <ApiWakeRetry />}
      <p className="state-help">Free preview се събужда автоматично. При по-бавен старт изчакването може да достигне около две минути; не е нужно да свързвате профила отново.</p>
    </main>
  );
}
