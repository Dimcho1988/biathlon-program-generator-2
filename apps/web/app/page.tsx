import { getTrainingStatus, type TrainingStatusResult } from "../lib/api";
import { Dashboard } from "../components/dashboard";
import { ErrorState } from "../components/error-state";

type PageResult =
  | { ok: true; value: TrainingStatusResult }
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

  try {
    result = { ok: true, value: await getTrainingStatus() };
  } catch (error) {
    result = {
      ok: false,
      message: error instanceof Error ? error.message : "Възникна неочаквана грешка.",
    };
  }

  const integrationActions = process.env.ONFLOWS_API_RESOURCE === "real";
  const notice = query.intervals ? notices[query.intervals] : undefined;
  return result.ok
    ? <Dashboard {...result.value} integrationActions={integrationActions} notice={notice} />
    : <ErrorState message={result.message} integrationActions={integrationActions} notice={notice} />;
}
