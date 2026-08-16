export function ErrorState({ message, integrationActions = false, refreshAvailable = true, notice }: {
  message: string;
  integrationActions?: boolean;
  refreshAvailable?: boolean;
  notice?: string;
}) {
  return (
    <main className="state-page" role="alert">
      <span className="state-icon" aria-hidden="true">!</span>
      <p className="eyebrow">Данните не са заредени</p>
      <h1>Не можем да покажем тренировъчния статус</h1>
      <p className="muted">{message}</p>
      {notice && <p className="connection-notice">{notice}</p>}
      {integrationActions && <div className="integration-actions">
        <a className="action-button" href="/api/integrations/intervals/connect">Свържи Intervals</a>
        {refreshAvailable && <form action="/api/integrations/intervals/refresh" method="post"><button className="action-button secondary" type="submit">Обнови реалните данни</button></form>}
      </div>}
      <p className="state-help">Проверете API адреса и опитайте отново. Демо данни не се зареждат автоматично.</p>
    </main>
  );
}
