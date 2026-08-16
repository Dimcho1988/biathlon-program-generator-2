import { getLoadHistory, getRecoveryHistory, getTrainingStatus, type TrainingStatusResult } from "../lib/api";
import type { LoadHistory } from "../lib/load-history";
import type { RecoveryHistory } from "../lib/recovery-history";
import { Dashboard } from "../components/dashboard";
import { ErrorState } from "../components/error-state";

type PageResult =
  | { ok: true; value: TrainingStatusResult; loadHistory: LoadHistory | null; recoveryHistory: RecoveryHistory | null; loadHistoryMessage?: string; recoveryHistoryMessage?: string }
  | { ok: false; message: string };

const notices: Record<string, string> = {
  connected: "Intervals профилът е свързан защитено. Стартирайте първото обновяване.",
  error: "Свързването с Intervals не завърши. Опитайте отново.",
  "connect-error": "Връзката с OAuth услугата не е достъпна в момента.",
  "refresh-error": "Обновяването не завърши успешно. Последният валиден анализ е запазен.",
};

export default async function Page({ searchParams }: { searchParams: Promise<{ intervals?: string }> }) {
  const query = await searchParams;
  let result: PageResult;

  const [statusResult, historyResult, recoveryResult] = await Promise.allSettled([
    getTrainingStatus(),
    getLoadHistory(),
    getRecoveryHistory(),
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

  const integrationActions = process.env.ONFLOWS_API_RESOURCE === "real";
  const notice = query.intervals === "connected" && result.ok && result.loadHistory
    ? undefined
    : query.intervals ? notices[query.intervals] : undefined;
  return result.ok
    ? <Dashboard {...result.value} loadHistory={result.loadHistory} recoveryHistory={result.recoveryHistory} loadHistoryMessage={result.loadHistoryMessage} recoveryHistoryMessage={result.recoveryHistoryMessage} integrationActions={integrationActions} notice={notice} />
    : <ErrorState message={result.message} integrationActions={integrationActions} notice={notice} />;
}
