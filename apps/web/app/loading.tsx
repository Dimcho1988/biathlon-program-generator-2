export default function Loading() {
  return (
    <main className="state-page" aria-busy="true" aria-live="polite">
      <div className="loader" aria-hidden="true" />
      <p className="eyebrow">onFlows</p>
      <h1>Зареждаме данните…</h1>
      <p className="muted">Можеш да отвориш друг раздел от менюто. Първото зареждане след пауза може да отнеме повече време.</p>
    </main>
  );
}
