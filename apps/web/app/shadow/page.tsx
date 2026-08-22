import Link from "next/link";
import { ShadowActivityPanel } from "../../components/shadow-activity-panel";
import { currentAthleteAlias } from "../../lib/athlete-session";
import { getActivityShadow, getActivityShadowIndex } from "../../lib/api";

export default async function ShadowPage({
  searchParams,
}: {
  searchParams: Promise<{ activity_ref?: string }>;
}) {
  const alias = await currentAthleteAlias();
  if (!alias) return <main><p>Необходима е активна защитена сесия.</p></main>;
  const query = await searchParams;
  const index = await getActivityShadowIndex(alias);
  const selected = query.activity_ref ?? index.activities[0]?.activity_ref;
  const payload = selected ? await getActivityShadow(alias, selected) : null;
  return (
    <main style={{ maxWidth: 1500, margin: "0 auto", padding: "2rem" }}>
      <p><Link href="/">← onFlows</Link></p>
      <h1>Experimental shadow models</h1>
      <p>
        Vflat B65 и HRmod v4 са диагностични. Тези резултати не променят
        canonical training load, recovery или реалните HR зони.
      </p>
      <nav aria-label="Shadow activities" style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 24 }}>
        {index.activities.map((activity) => (
          <Link key={activity.activity_ref} href={`/shadow?activity_ref=${activity.activity_ref}`}>
            {activity.activity_ref.slice(-8)}
          </Link>
        ))}
      </nav>
      {payload ? <ShadowActivityPanel payload={payload} /> : <p>Няма съхранени shadow резултати. Стартирайте refresh.</p>}
    </main>
  );
}
