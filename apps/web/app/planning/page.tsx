import { ErrorState } from "../../components/error-state";
import { PlanningProfileForm } from "../../components/planning-profile-form";
import { currentAuthorizedAthlete } from "../../lib/account-access";
import { multiProfileMode } from "../../lib/athlete-session";
import {
  getAthletePlanningProfile,
  getMesocycleAccentPreferences,
  getPlanningCalendar,
  getPlanningMethodology,
} from "../../lib/api";

const notices: Record<string, string> = {
  saved: "Индивидуалният профил за планиране е запазен.",
  "accents-saved": "Правилото за мезоцикличните акценти е запазено.",
  "calendar-saved": "Календарът за планиране е запазен.",
  invalid: "Структурата съдържа липсващи или несъвместими стойности.",
  "accents-invalid": "Правилото за акцентите съдържа несъвместими стойности.",
  "calendar-invalid": "Календарът съдържа липсващи или несъвместими стойности.",
  error: "Профилът за планиране не беше запазен. Опитайте отново.",
  "accents-error": "Правилото за акцентите не беше запазено. Опитайте отново.",
  "calendar-error": "Календарът не беше запазен. Опитайте отново.",
  forbidden: "Този профил е достъпен само за преглед. Главният треньор може да даде право за редакция.",
};

export default async function PlanningPage({
  searchParams,
}: {
  searchParams: Promise<{ planning?: string }>;
}) {
  const query = await searchParams;
  const athlete = multiProfileMode() ? await currentAuthorizedAthlete() : null;
  const athleteAlias = athlete?.athleteAlias ?? null;
  if (!athlete || !athleteAlias) return <ErrorState
    message="Няма активна защитена сесия за спортист."
    integrationActions
    refreshAvailable={false}
  />;
  if (!athlete.canEditPlan) return <ErrorState
    message="Нямаш право да променяш плана на този спортист."
    profileSelectionAvailable
    retryAvailable
  />;
  let result;
  let methodology;
  let accentPreferences;
  let planningCalendar;
  try {
    [result, methodology, accentPreferences, planningCalendar] = await Promise.all([
      getAthletePlanningProfile(athleteAlias),
      getPlanningMethodology(athleteAlias),
      getMesocycleAccentPreferences(athleteAlias),
      getPlanningCalendar(athleteAlias),
    ]);
  } catch (error) {
    return <ErrorState
      message={error instanceof Error ? error.message : "Профилът за планиране не е достъпен."}
      integrationActions={false}
      retryAvailable
    />;
  }
  return <PlanningProfileForm
    profile={result.profile}
    methodology={methodology}
    accentPreferences={accentPreferences}
    planningCalendar={planningCalendar}
    notice={query.planning ? notices[query.planning] : undefined}
  />;
}
