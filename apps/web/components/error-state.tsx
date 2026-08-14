export function ErrorState({ message }: { message: string }) {
  return (
    <main className="state-page" role="alert">
      <span className="state-icon" aria-hidden="true">!</span>
      <p className="eyebrow">Данните не са заредени</p>
      <h1>Не можем да покажем тренировъчния статус</h1>
      <p className="muted">{message}</p>
      <p className="state-help">Проверете API адреса и опитайте отново. Демо данни не се зареждат автоматично.</p>
    </main>
  );
}
