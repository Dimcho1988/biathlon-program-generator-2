import { getTrainingStatus, type TrainingStatusResult } from "../lib/api";
import { Dashboard } from "../components/dashboard";
import { ErrorState } from "../components/error-state";

type PageResult =
  | { ok: true; value: TrainingStatusResult }
  | { ok: false; message: string };

export default async function Page() {
  let result: PageResult;

  try {
    result = { ok: true, value: await getTrainingStatus() };
  } catch (error) {
    result = {
      ok: false,
      message: error instanceof Error ? error.message : "Възникна неочаквана грешка.",
    };
  }

  return result.ok ? <Dashboard {...result.value} /> : <ErrorState message={result.message} />;
}
