import Link from "next/link";
import { ShadowActivityPanel } from "../../components/shadow-activity-panel";
import { currentAthleteAlias } from "../../lib/athlete-session";
import {
  getActivityShadow,
  getActivityShadowIndex,
  type ActivityShadowIndex,
} from "../../lib/api";

const shortReference = (value: string) => value.slice(-8).toUpperCase();

function ShadowUnavailable({ message }: { message: string }) {
  return (
    <main className="shadow-page">
      <p><Link href="/">← Тренировъчен статус</Link></p>
      <section className="shadow-empty">
        <p className="eyebrow">Experimental / shadow</p>
        <h1>Raw ↔ Shadow сравнение</h1>
        <p>{message}</p>
        <Link className="action-button" href="/shadow">Опитай отново</Link>
      </section>
    </main>
  );
}

export default async function ShadowPage({
  searchParams,
}: {
  searchParams: Promise<{ activity_ref?: string }>;
}) {
  const alias = await currentAthleteAlias();
  if (!alias) return <ShadowUnavailable message="Необходима е активна защитена сесия." />;

  let index: ActivityShadowIndex;
  try {
    index = await getActivityShadowIndex(alias);
  } catch (error) {
    const detail = error instanceof Error ? error.message : "Shadow данните временно не са достъпни.";
    return <ShadowUnavailable message={`${detail} Ако API услугата се събужда, изчакайте и опитайте отново.`} />;
  }

  const query = await searchParams;
  const requestedIndex = index.activities.findIndex((activity) => activity.activity_ref === query.activity_ref);
  const selectedIndex = requestedIndex >= 0 ? requestedIndex : 0;
  const selected = index.activities[selectedIndex]?.activity_ref;
  let payload: Record<string, unknown> | null = null;
  let payloadError: string | null = null;
  if (selected) {
    try {
      payload = await getActivityShadow(alias, selected);
    } catch (error) {
      payloadError = error instanceof Error ? error.message : "Избраната активност временно не е достъпна.";
    }
  }
  const previous = selectedIndex > 0 ? index.activities[selectedIndex - 1] : null;
  const next = selectedIndex + 1 < index.activities.length ? index.activities[selectedIndex + 1] : null;

  return (
    <main className="shadow-page">
      <p><Link href="/">← Тренировъчен статус</Link></p>
      <header className="shadow-hero">
        <div>
          <p className="eyebrow">Experimental / shadow</p>
          <h1>Raw ↔ Shadow сравнение</h1>
          <p className="shadow-intro">
            Vflat B65 и HRmod v4 са отделни диагностични канали. Те не променят
            тренировъчното натоварване, recovery, 7/40 или реалните HR зони.
          </p>
        </div>
        <span className="configuration-badge">Не влияе на основния модел</span>
      </header>

      {index.activities.length > 0 ? (
        <section className="shadow-picker" aria-label="Избор на активност">
          <form method="get">
            <label htmlFor="shadow-activity">Активност за сравнение</label>
            <select id="shadow-activity" name="activity_ref" defaultValue={selected}>
              {index.activities.map((activity, position) => (
                <option key={activity.activity_ref} value={activity.activity_ref}>
                  {`Активност ${position + 1} от ${index.activities.length} · ${shortReference(activity.activity_ref)}`}
                </option>
              ))}
            </select>
            <button className="action-button" type="submit">Покажи</button>
          </form>
          <nav aria-label="Предишна и следваща активност">
            {previous ? <Link href={`/shadow?activity_ref=${previous.activity_ref}`}>← Предишна</Link> : <span>← Предишна</span>}
            <strong>{selectedIndex + 1} / {index.activities.length}</strong>
            {next ? <Link href={`/shadow?activity_ref=${next.activity_ref}`}>Следваща →</Link> : <span>Следваща →</span>}
          </nav>
        </section>
      ) : null}

      {payloadError ? (
        <section className="shadow-empty"><h2>Активността не се зареди</h2><p>{payloadError}</p></section>
      ) : payload && selected ? (
        <ShadowActivityPanel payload={payload} activityRef={selected} />
      ) : (
        <section className="shadow-empty">
          <h2>Няма съхранени shadow резултати</h2>
          <p>Стартирайте „Обнови данните“ от основното приложение.</p>
        </section>
      )}
    </main>
  );
}
