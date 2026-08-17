import { getAthleteSettings, getLoadHistory, getRecoveryHistory, getTrainingStatus, type TrainingStatusResult } from "../lib/api";
import type { LoadHistory } from "../lib/load-history";
import type { RecoveryHistory } from "../lib/recovery-history";
import { Dashboard } from "../components/dashboard";
import { ErrorState } from "../components/error-state";
import { currentAthleteAlias, multiProfileMode } from "../lib/athlete-session";
import { AthleteSettingsForm } from "../components/athlete-settings-form";
import { redirect } from "next/navigation";

type PageResult =
  | { ok: true; value: TrainingStatusResult; loadHistory: LoadHistory | null; recoveryHistory: RecoveryHistory | null; loadHistoryMessage?: string; recoveryHistoryMessage?: string }
  | { ok: false; message: string };

const notices: Record<string, string> = {
  connected: "Intervals профилът е свързан защитено. Стартирайте първото обновяване.",
  error: "Свързването с Intervals не завърши. Опитайте отново.",
  "error-callback": "Intervals върна непълен или отказан OAuth отговор. Опитайте свързването отново.",
  "error-state": "Защитената OAuth заявка е изтекла или вече е използвана. Стартирайте ново свързване.",
  "error-exchange": "Intervals не издаде валиден read-only достъп. Опитайте свързването отново.",
  "error-permissions": "Intervals не предостави всички необходими права само за четене.",
  "error-binding": "Този Intervals профил вече е свързан с друга защитена сесия.",
  "error-identity": "Не успяхме да създадем защитена самоличност за спортиста. Опитайте отново.",
  "error-storage": "Защитеното хранилище временно не е достъпно. Опитайте отново.",
  "error-authorization": "Свързването не можа да бъде записано. Опитайте отново.",
  "error-session": "Intervals потвърди достъпа, но защитената сесия не беше създадена.",
  "connect-error": "Връзката с OAuth услугата не е достъпна в момента.",
  "connect-start-error": "OAuth връзката с Intervals не стартира. Опитайте отново.",
  "session-error": "Intervals потвърди връзката, но защитената сесия не беше създадена.",
  "refresh-error": "Обновяването не завърши успешно. Последният валиден анализ е запазен.",
  "session-required": "Свържете Intervals профила, за да отворите неговите данни.",
};

const settingsNotices: Record<string, string> = {
  saved: "Индивидуалните настройки са запазени. Обновете реалните данни, за да преизчислите анализа.",
  invalid: "Границите трябва да са шест последователно нарастващи цели стойности между 30 и 240 уд/мин.",
  error: "Настройките не бяха запазени. Опитайте отново.",
};

export default async function Page({ searchParams }: { searchParams: Promise<{ intervals?: string; settings?: string; wake?: string }> }) {
  const query = await searchParams;
  let result: PageResult;
  const integrationActions = process.env.ONFLOWS_API_RESOURCE === "real";
  const multiProfile = integrationActions && multiProfileMode();
  const athleteAlias = multiProfile ? await currentAthleteAlias() : null;

  if (multiProfile && !athleteAlias) {
    const notice = query.intervals ? notices[query.intervals] : undefined;
    return <ErrorState
      message="Няма активна защитена сесия за спортист."
      integrationActions
      refreshAvailable={false}
      notice={notice}
    />;
  }

  if (multiProfile && athleteAlias && query.wake !== "ready") {
    const baseUrl = process.env.ONFLOWS_API_BASE_URL;
    if (baseUrl) {
      const wakeUrl = new URL("/api/v2/wake", baseUrl);
      if (query.intervals) wakeUrl.searchParams.set("intervals", query.intervals);
      if (query.settings) wakeUrl.searchParams.set("settings", query.settings);
      redirect(wakeUrl.toString());
    }
  }

  if (query.settings === "edit" && multiProfile && athleteAlias) {
    let settings: Awaited<ReturnType<typeof getAthleteSettings>> | null = null;
    let settingsError: string | null = null;
    try {
      settings = await getAthleteSettings(athleteAlias);
    } catch (error) {
      settingsError = error instanceof Error ? error.message : "Настройките на профила не са достъпни.";
    }
    if (!settings) {
      return <ErrorState
        message={settingsError ?? "Настройките на профила не са достъпни."}
        integrationActions={integrationActions}
        connectAvailable={false}
        retryAvailable
        notice={settingsNotices.error}
      />;
    }
    return <AthleteSettingsForm
      editing
      initialBounds={settings.hr_zone_bounds_bpm}
      initialTimezone={settings.timezone}
    />;
  }

  const [statusResult, historyResult, recoveryResult] = await Promise.allSettled([
    getTrainingStatus(athleteAlias ?? undefined),
    getLoadHistory(athleteAlias ?? undefined),
    getRecoveryHistory(athleteAlias ?? undefined),
  ]);
  if (statusResult.status === "fulfilled") {
    result = {
      ok: true,
      value: statusResult.value,
      loadHistory: historyResult.status === "fulfilled" ? historyResult.value : null,
      recoveryHistory: recoveryResult.status === "fulfilled" ? recoveryResult.value : null,
      loadHistoryMessage: historyResult.status === "rejected" ? (historyResult.reason instanceof Error ? historyResult.reason.message : "Историята на натоварването не е достъпна.") : undefined,
      recoveryHistoryMessage: recoveryResult.status === "rejected" ? (recoveryResult.reason instanceof Error ? recoveryResult.reason.message : "Recovery историята не е достъпна.") : undefined,
    };
  } else {
    const error = statusResult.reason;
    result = {
      ok: false,
      message: error instanceof Error ? error.message : "Възникна неочаквана грешка.",
    };
  }

  const settingsNotice = query.settings ? settingsNotices[query.settings] : undefined;
  let athleteSettingsRequired = false;
  if (!result.ok && multiProfile && athleteAlias) {
    try {
      const settings = await getAthleteSettings(athleteAlias);
      athleteSettingsRequired = !settings.configured;
    } catch {
      // Keep the normal API error visible when the settings service is unavailable.
    }
  }
  if (athleteSettingsRequired)
    return <AthleteSettingsForm notice={settingsNotice} />;

  const integrationNotice = query.intervals === "connected" && result.ok && result.loadHistory
    ? undefined
    : query.intervals ? notices[query.intervals] : undefined;
  const notice = settingsNotice ?? integrationNotice;
  return result.ok
    ? <Dashboard {...result.value} loadHistory={result.loadHistory} recoveryHistory={result.recoveryHistory} loadHistoryMessage={result.loadHistoryMessage} recoveryHistoryMessage={result.recoveryHistoryMessage} integrationActions={integrationActions} sessionActions={multiProfile} notice={notice} />
    : <ErrorState
      message={result.message}
      integrationActions={integrationActions}
      connectAvailable={!multiProfile || !athleteAlias}
      retryAvailable={Boolean(multiProfile && athleteAlias)}
      notice={notice}
    />;
}
