import { currentAuthorizedAthlete } from "../../lib/account-access";
import { getEnergyHistory } from "../../lib/api";
import { ErrorState } from "../../components/error-state";
import { EnergyDashboard } from "../../components/energy-dashboard";
import type { EnergyHistory } from "../../lib/energy";
import "./energy.css";
export const dynamic = "force-dynamic";
export default async function EnergyPage({
  searchParams,
}: {
  searchParams: Promise<{ start?: string; end?: string }>;
}) {
  const access = await currentAuthorizedAthlete();
  if (!access || !access.canViewRecovery)
    return (
      <ErrorState
        message="Влезте и изберете спортист със споделени данни за възстановяване."
        refreshAvailable={false}
      />
    );
  const q = await searchParams;
  let data: EnergyHistory;
  try {
    data = await getEnergyHistory(access.athleteAlias, q.start, q.end);
    if (!(access.isOwner || access.canViewPlan)) data = { ...data, race_preparation: [] };
  } catch (e) {
    return (
      <ErrorState
        message={
          e instanceof Error
            ? e.message
            : "Енергийният отчет временно е недостъпен."
        }
        retryAvailable
        retryHref="/energy"
      />
    );
  }
  return (
    <main className="energy-page">
      <header>
        <p className="eyebrow">
          {access.displayName} · {data.timezone}
        </p>
        <h1>Енергия и хранене</h1>
        <p>Оценен разход по участъци и хранителни цели за избрания ден.</p>
      </header>
      <EnergyDashboard
        data={data}
        canEdit={access.isOwner || access.canEditPlan}
      />
    </main>
  );
}
