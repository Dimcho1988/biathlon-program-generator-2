import { ErrorState } from "../../components/error-state";
import { PlanningProfileForm } from "../../components/planning-profile-form";
import { currentAthleteAlias, multiProfileMode } from "../../lib/athlete-session";
import { getAthletePlanningProfile } from "../../lib/api";

const notices: Record<string, string> = {
  saved: "Индивидуалният профил за планиране е запазен.",
  invalid: "Структурата съдържа липсващи или несъвместими стойности.",
  error: "Профилът за планиране не беше запазен. Опитайте отново.",
};

export default async function PlanningPage({
  searchParams,
}: {
  searchParams: Promise<{ planning?: string }>;
}) {
  const query = await searchParams;
  const athleteAlias = multiProfileMode() ? await currentAthleteAlias() : null;
  if (!athleteAlias) return <ErrorState
    message="Няма активна защитена сесия за спортист."
    integrationActions
    refreshAvailable={false}
  />;
  let result;
  try {
    result = await getAthletePlanningProfile(athleteAlias);
  } catch (error) {
    return <ErrorState
      message={error instanceof Error ? error.message : "Профилът за планиране не е достъпен."}
      integrationActions={false}
      retryAvailable
    />;
  }
  return <PlanningProfileForm
    profile={result.profile}
    notice={query.planning ? notices[query.planning] : undefined}
  />;
}
