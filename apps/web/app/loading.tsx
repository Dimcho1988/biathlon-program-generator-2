export default function Loading() {
  return (
    <main className="state-page" aria-busy="true" aria-live="polite">
      <div className="loader" aria-hidden="true" />
      <p className="eyebrow">onFlows · анализ</p>
      <h1>Зареждане на тренировъчния статус…</h1>
      <p className="muted">Проверяваме данните и версията на договора.</p>
    </main>
  );
}
